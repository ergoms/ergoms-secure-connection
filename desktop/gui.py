"""Lightweight tkinter VPN-style GUI + system tray for ops-content."""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from desktop import __version__
from desktop.client import OpsClient
from desktop.config_io import (
    apply_dotenv,
    load_config,
    load_dotenv,
    save_config,
    save_dotenv,
)
from desktop.paths import Paths, bundle_dir
from desktop.watchdog import TunnelWatchdog, infer_desired_on


C_BG = "#10141a"
C_SURFACE = "#1a2030"
C_SURFACE2 = "#252d3d"
C_BTN = "#343e52"
C_BTN_HOVER = "#44506a"
C_BORDER = "#7a869c"
C_TEXT = "#f2f4f8"
C_MUTED = "#9aa3b5"
C_ACCENT = "#2dd4a8"
C_ACCENT_DIM = "#1a9e7a"
C_DANGER = "#f07178"
C_WARN = "#e6c07b"
C_OK = "#2dd4a8"
C_SELECT = "#1f6b55"

WIN_W = 400
WIN_H = 640

# UI labels → stored values
CHOICES: dict[str, list[tuple[str, str]]] = {
    "MODE": [("Туннель", "socks"), ("VPS", "vps")],
    "SOCKS_SCOPE": [("Всё", "full"), ("GitHub + Cursor", "github")],
    "TUN": [("Выкл", "0"), ("Вкл", "1")],
    "TUN_ELEVATE": [("Нет", "0"), ("Да", "1")],
    "WATCHDOG": [("Выкл", "0"), ("Вкл", "1")],
    "proxy_bypass_via": [("Напрямую", "direct"), ("Через Squid", "corporate")],
}


def _bind_clipboard(widget: tk.Widget) -> None:
    """Make Ctrl+C/V/X/A work reliably on Windows (incl. Russian layout)."""

    def _copy(_event: tk.Event | None = None) -> str | None:
        try:
            if isinstance(widget, (tk.Entry, ttk.Entry)):
                if widget.selection_present():
                    widget.clipboard_clear()
                    widget.clipboard_append(widget.selection_get())
            elif isinstance(widget, tk.Text):
                widget.clipboard_clear()
                widget.clipboard_append(widget.get("sel.first", "sel.last"))
        except tk.TclError:
            pass
        return "break"

    def _cut(event: tk.Event | None = None) -> str | None:
        _copy(event)
        try:
            if isinstance(widget, (tk.Entry, ttk.Entry)):
                if widget.selection_present():
                    widget.delete("sel.first", "sel.last")
            elif isinstance(widget, tk.Text):
                widget.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        return "break"

    def _paste(_event: tk.Event | None = None) -> str | None:
        try:
            data = widget.clipboard_get()
        except tk.TclError:
            return "break"
        try:
            if isinstance(widget, (tk.Entry, ttk.Entry)):
                try:
                    if widget.selection_present():
                        widget.delete("sel.first", "sel.last")
                except tk.TclError:
                    pass
                widget.insert("insert", data)
            elif isinstance(widget, tk.Text):
                try:
                    widget.delete("sel.first", "sel.last")
                except tk.TclError:
                    pass
                widget.insert("insert", data)
        except tk.TclError:
            pass
        return "break"

    def _select_all(_event: tk.Event | None = None) -> str | None:
        try:
            if isinstance(widget, (tk.Entry, ttk.Entry)):
                widget.selection_range(0, "end")
                widget.icursor("end")
            elif isinstance(widget, tk.Text):
                widget.tag_add("sel", "1.0", "end-1c")
        except tk.TclError:
            pass
        return "break"

    for seq, fn in (
        ("<Control-c>", _copy),
        ("<Control-C>", _copy),
        ("<<Copy>>", _copy),
        ("<Control-x>", _cut),
        ("<Control-X>", _cut),
        ("<<Cut>>", _cut),
        ("<Control-v>", _paste),
        ("<Control-V>", _paste),
        ("<<Paste>>", _paste),
        ("<Control-a>", _select_all),
        ("<Control-A>", _select_all),
        # Russian layout: same physical keys
        ("<Control-KeyPress>", None),
    ):
        if fn is not None:
            widget.bind(seq, fn)

    def _ru_keys(event: tk.Event) -> str | None:
        # Cyrillic counterparts of c/v/x/a on Russian keyboard
        key = (event.keysym or "").lower()
        char = event.char or ""
        if key in ("c",) or char in ("с", "С"):
            return _copy(event)
        if key in ("v",) or char in ("м", "М"):
            return _paste(event)
        if key in ("x",) or char in ("ч", "Ч"):
            return _cut(event)
        if key in ("a",) or char in ("ф", "Ф"):
            return _select_all(event)
        return None

    widget.bind("<Control-KeyPress>", _ru_keys)


class Segment(tk.Frame):
    """Two/three option toggle — no dropdowns."""

    def __init__(
        self,
        master: tk.Misc,
        choices: list[tuple[str, str]],
        variable: tk.StringVar,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, bg=C_SURFACE2, highlightthickness=0, **kwargs)
        self._var = variable
        self._choices = choices
        self._btns: list[tk.Label] = []
        for i, (label, value) in enumerate(choices):
            lbl = tk.Label(
                self,
                text=label,
                bg=C_BTN,
                fg=C_TEXT,
                font=("Segoe UI", 10, "bold"),
                padx=10,
                pady=8,
                cursor="hand2",
            )
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0 if i == 0 else 1, 0))
            lbl.bind("<Button-1>", lambda _e, v=value: self._pick(v))
            self._btns.append(lbl)
        self._var.trace_add("write", lambda *_: self._paint())
        self._paint()

    def _pick(self, value: str) -> None:
        self._var.set(value)

    def _paint(self) -> None:
        cur = self._var.get()
        for lbl, (_label, value) in zip(self._btns, self._choices):
            if value == cur:
                lbl.configure(bg=C_SELECT, fg=C_TEXT)
            else:
                lbl.configure(bg=C_BTN, fg=C_MUTED)


class App:
    def __init__(self) -> None:
        self.paths = Paths()
        self.paths.ensure_dirs()
        apply_dotenv(self.paths.env_path)
        self.log_q: queue.Queue[str] = queue.Queue()
        self.client = OpsClient(paths=self.paths, log=self._enqueue_log)

        self._busy = False
        self._tray = None
        self._tray_thread: threading.Thread | None = None
        self._closing = False
        self._active = False
        self._tun = False
        self._page = "home"
        self._last_status_sig = ""
        self._status_busy = False
        self._pulse_job: str | None = None
        self._pulse_on = False
        self._pages: dict[str, tk.Frame] = {}
        self._nav: dict[str, tk.Label] = {}
        self.watchdog = TunnelWatchdog(
            self.client,
            log=self._enqueue_log,
            on_notify=self._tray_notify,
            should_skip=lambda: self._busy or self._closing,
        )

        self.root = tk.Tk()
        self.root.title("ops-content")
        self.root.geometry(f"{WIN_W}x{WIN_H}")
        self.root.minsize(WIN_W, WIN_H)
        self.root.maxsize(WIN_W, WIN_H)
        self.root.resizable(False, False)
        self.root.configure(bg=C_BG)
        self.root.option_add("*Font", "{Segoe UI} 10")
        self._disable_maximize()
        self._set_icon()
        self._style()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._enqueue_log(f"ops-content {__version__}")
        self._enqueue_log(f"данные: {self.paths.root}")

        if not self.paths.config_path.is_file() or not self.paths.env_path.is_file():
            try:
                self.client.init()
                self._enqueue_log("Созданы config.json и .env")
            except Exception as exc:  # noqa: BLE001
                self._enqueue_log(f"инициализация: {exc}")

        self.root.after(150, self._drain_log)
        self.root.after(300, self._refresh_status)
        self.root.after(700, self._start_tray)
        self.root.after(1200, self._start_watchdog)

    def _disable_maximize(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes

            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -16)
            ctypes.windll.user32.SetWindowLongW(hwnd, -16, style & ~0x00010000)
        except Exception:
            pass

    def _set_icon(self) -> None:
        ico = Path(__file__).with_name("app_icon.ico")
        if not ico.is_file():
            ico = bundle_dir() / "desktop" / "app_icon.ico"
        if ico.is_file():
            try:
                self.root.iconbitmap(default=str(ico))
            except tk.TclError:
                pass

    def _style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TScrollbar", background=C_BTN, troughcolor=C_BG, bordercolor=C_BG, arrowcolor=C_TEXT)
        style.map("TScrollbar", background=[("active", C_BORDER)])

    # ── chrome ──────────────────────────────────────────────────────────

    def _build(self) -> None:
        top = tk.Frame(self.root, bg=C_BG)
        top.pack(fill=tk.X, padx=20, pady=(14, 6))
        tk.Label(
            top, text="ops-content", bg=C_BG, fg=C_TEXT, font=("Segoe UI", 16, "bold")
        ).pack(side=tk.LEFT)
        tk.Label(
            top, text=f"v{__version__}", bg=C_BG, fg=C_MUTED, font=("Segoe UI", 10)
        ).pack(side=tk.RIGHT)

        self.body = tk.Frame(self.root, bg=C_BG)
        self.body.pack(fill=tk.BOTH, expand=True)

        for key, builder in (
            ("home", self._build_home),
            ("settings", self._build_settings),
            ("log", self._build_log),
        ):
            fr = tk.Frame(self.body, bg=C_BG)
            fr.place(relx=0, rely=0, relwidth=1, relheight=1)
            builder(fr)
            self._pages[key] = fr

        nav = tk.Frame(self.root, bg=C_SURFACE, height=56)
        nav.pack(fill=tk.X, side=tk.BOTTOM)
        nav.pack_propagate(False)
        for i, (key, label) in enumerate(
            (("home", "Домой"), ("settings", "Настройки"), ("log", "Журнал"))
        ):
            cell = tk.Frame(nav, bg=C_SURFACE)
            cell.place(relx=i / 3, rely=0, relwidth=1 / 3, relheight=1)
            lbl = tk.Label(
                cell,
                text=label,
                bg=C_SURFACE,
                fg=C_MUTED,
                font=("Segoe UI", 10, "bold"),
                cursor="hand2",
                pady=16,
            )
            lbl.pack(fill=tk.BOTH, expand=True, padx=6, pady=8)
            lbl.bind("<Button-1>", lambda _e, k=key: self._show_page(k))
            self._nav[key] = lbl

        self._show_page("home")

    def _show_page(self, key: str) -> None:
        self._page = key
        self._pages[key].lift()
        for k, lbl in self._nav.items():
            if k == key:
                lbl.configure(bg=C_BTN, fg=C_TEXT)
            else:
                lbl.configure(bg=C_SURFACE, fg=C_MUTED)

    def _card(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(parent, bg=C_SURFACE, highlightthickness=0, bd=0)

    def _btn(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        primary: bool = False,
        danger: bool = False,
        fill: bool = True,
    ) -> tk.Label:
        if danger:
            bg, fg, hover = C_DANGER, C_TEXT, "#c45c63"
        elif primary:
            bg, fg, hover = C_ACCENT, "#04140f", C_ACCENT_DIM
        else:
            bg, fg, hover = C_BTN, C_TEXT, C_BTN_HOVER
        lbl = tk.Label(
            parent,
            text=text,
            bg=bg,
            fg=fg,
            font=("Segoe UI", 11, "bold"),
            padx=12,
            pady=12,
            cursor="hand2",
            highlightthickness=1 if not primary and not danger else 0,
            highlightbackground=C_BORDER,
            highlightcolor=C_BORDER,
        )
        if fill:
            lbl.pack(fill=tk.X)

        def enter(_e: tk.Event) -> None:
            if not self._busy or lbl is self.btn_power:
                lbl.configure(bg=hover)

        def leave(_e: tk.Event) -> None:
            lbl.configure(bg=bg)

        def click(_e: tk.Event) -> None:
            if getattr(lbl, "_disabled", False):
                return
            command()

        lbl.bind("<Enter>", enter)
        lbl.bind("<Leave>", leave)
        lbl.bind("<Button-1>", click)
        # store colors for later reconfigure
        lbl._bg = bg  # type: ignore[attr-defined]
        lbl._fg = fg  # type: ignore[attr-defined]
        lbl._hover = hover  # type: ignore[attr-defined]
        return lbl

    def _recolor_btn(self, lbl: tk.Label, *, primary: bool = False, danger: bool = False, text: str) -> None:
        if danger:
            bg, fg, hover = C_DANGER, C_TEXT, "#c45c63"
        elif primary:
            bg, fg, hover = C_ACCENT, "#04140f", C_ACCENT_DIM
        else:
            bg, fg, hover = C_BTN, C_TEXT, C_BTN_HOVER
        lbl._bg = bg  # type: ignore[attr-defined]
        lbl._fg = fg  # type: ignore[attr-defined]
        lbl._hover = hover  # type: ignore[attr-defined]
        lbl.configure(
            text=text,
            bg=bg,
            fg=fg,
            highlightthickness=0 if primary or danger else 1,
        )

    # ── pages ───────────────────────────────────────────────────────────

    def _build_home(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=C_BG)
        wrap.pack(fill=tk.BOTH, expand=True, padx=22, pady=8)

        card = self._card(wrap)
        card.pack(fill=tk.X, pady=(4, 14))

        self.ring = tk.Label(card, text="●", bg=C_SURFACE, fg=C_MUTED, font=("Segoe UI", 42))
        self.ring.pack(pady=(22, 2))
        self.status_title = tk.Label(
            card, text="Отключено", bg=C_SURFACE, fg=C_TEXT, font=("Segoe UI", 22, "bold")
        )
        self.status_title.pack()
        self.status_sub = tk.Label(
            card, text="Нажмите «Подключить»", bg=C_SURFACE, fg=C_MUTED, font=("Segoe UI", 10)
        )
        self.status_sub.pack(pady=(2, 20))

        self.btn_power = self._btn(wrap, "Подключить", self._toggle_connection, primary=True)
        self.btn_power.configure(pady=14, font=("Segoe UI", 13, "bold"))

        meta = self._card(wrap)
        meta.pack(fill=tk.X, pady=(14, 10))
        self.meta_rows: dict[str, tk.Label] = {}
        for i, (key, label) in enumerate(
            (("mode", "Режим"), ("target", "Сервер"), ("scope", "Область"))
        ):
            row = tk.Frame(meta, bg=C_SURFACE)
            row.pack(fill=tk.X, padx=16, pady=(12 if i == 0 else 4, 12 if i == 2 else 4))
            tk.Label(row, text=label, bg=C_SURFACE, fg=C_MUTED, font=("Segoe UI", 10)).pack(
                side=tk.LEFT
            )
            val = tk.Label(row, text="—", bg=C_SURFACE, fg=C_TEXT, font=("Segoe UI", 10))
            val.pack(side=tk.RIGHT)
            self.meta_rows[key] = val

        quick = tk.Frame(wrap, bg=C_BG)
        quick.pack(fill=tk.X, pady=(4, 0))
        for i in range(3):
            quick.columnconfigure(i, weight=1)

        self.btn_tun = self._mk_quick(quick, 0, "TUN вкл", self._toggle_tun)
        self._mk_quick(quick, 1, "Проверка", self._probe)
        self._mk_quick(quick, 2, "Тест", lambda: self._run_bg(self.client.test_bypass))

        self.busy_lbl = tk.Label(wrap, text="", bg=C_BG, fg=C_MUTED, font=("Segoe UI", 10))
        self.busy_lbl.pack(pady=(14, 0))

    def _mk_quick(self, parent: tk.Frame, col: int, text: str, cmd: Callable[[], None]) -> tk.Label:
        cell = tk.Frame(parent, bg=C_BG)
        cell.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 4, 0 if col == 2 else 4))
        btn = self._btn(cell, text, cmd, primary=False)
        return btn

    def _build_settings(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=C_BG)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=(6, 10))
        bar.columnconfigure(0, weight=1)
        bar.columnconfigure(1, weight=1)
        left = tk.Frame(bar, bg=C_BG)
        left.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        right = tk.Frame(bar, bg=C_BG)
        right.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self._btn(left, "Загрузить", self._load_settings)
        self._btn(right, "Сохранить", self._save_settings, primary=True)

        canvas = tk.Canvas(parent, bg=C_BG, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(14, 0), pady=(4, 0))

        form = tk.Frame(canvas, bg=C_BG)
        win = canvas.create_window((0, 0), window=form, anchor="nw")

        def _form_cfg(_e: tk.Event | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _canvas_cfg(e: tk.Event) -> None:
            canvas.itemconfigure(win, width=e.width)

        form.bind("<Configure>", _form_cfg)
        canvas.bind("<Configure>", _canvas_cfg)

        def _wheel(e: tk.Event) -> None:
            if self._page != "settings":
                return
            delta = getattr(e, "delta", 0)
            if delta:
                canvas.yview_scroll(int(-delta / 120), "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        self.vars: dict[str, tk.StringVar] = {
            "MODE": tk.StringVar(value="socks"),
            "SOCKS_SCOPE": tk.StringVar(value="full"),
            "TUN": tk.StringVar(value="0"),
            "TUN_ELEVATE": tk.StringVar(value="1"),
            "WATCHDOG": tk.StringVar(value="1"),
            "HTTP_BRIDGE_PORT": tk.StringVar(value="1088"),
            "OPS_CONTENT_SECRET": tk.StringVar(value=""),
            "CORPORATE_PROXY": tk.StringVar(value=""),
            "corporate_proxy": tk.StringVar(value=""),
            "ssh_host": tk.StringVar(value=""),
            "ssh_user": tk.StringVar(value="root"),
            "ssh_port": tk.StringVar(value="443"),
            "ssh_identity": tk.StringVar(value=""),
            "ssh_socks": tk.StringVar(value="1080"),
            "worker_base_url": tk.StringVar(value=""),
            "proxy_bypass": tk.StringVar(value=""),
            "proxy_bypass_via": tk.StringVar(value="direct"),
            "sing_box_path": tk.StringVar(value=""),
        }

        sections: list[tuple[str, list[tuple[str, str, Any]]]] = [
            (
                "Режим",
                [
                    ("Режим работы", "MODE", "choice"),
                    ("Область трафика", "SOCKS_SCOPE", "choice"),
                    ("TUN автоматически", "TUN", "choice"),
                    ("Запрос прав админа", "TUN_ELEVATE", "choice"),
                    ("Автопереподключение", "WATCHDOG", "choice"),
                    ("Порт HTTP-моста", "HTTP_BRIDGE_PORT", None),
                ],
            ),
            (
                "Сервер",
                [
                    ("Адрес сервера", "ssh_host", None),
                    ("Пользователь SSH", "ssh_user", None),
                    ("Порт SSH", "ssh_port", None),
                    ("Ключ SSH", "ssh_identity", "file"),
                    ("Локальный порт SOCKS", "ssh_socks", None),
                ],
            ),
            (
                "Прокси и исключения",
                [
                    ("Секрет", "OPS_CONTENT_SECRET", None),
                    ("Корп. прокси (.env)", "CORPORATE_PROXY", None),
                    ("Корп. прокси (config)", "corporate_proxy", None),
                    ("Исключения (через запятую)", "proxy_bypass", None),
                    ("Исключения идут", "proxy_bypass_via", "choice"),
                    ("Адрес воркера", "worker_base_url", None),
                    ("Путь к sing-box", "sing_box_path", "file"),
                ],
            ),
        ]

        for title, fields in sections:
            tk.Label(
                form, text=title.upper(), bg=C_BG, fg=C_MUTED, font=("Segoe UI", 9, "bold"), anchor="w"
            ).pack(fill=tk.X, pady=(12, 6), padx=2)
            card = self._card(form)
            card.pack(fill=tk.X, pady=(0, 4))
            for fi, (label, key, kind) in enumerate(fields):
                tk.Label(
                    card, text=label, bg=C_SURFACE, fg=C_MUTED, font=("Segoe UI", 9), anchor="w"
                ).pack(fill=tk.X, padx=14, pady=(12 if fi == 0 else 8, 2))
                if kind == "choice":
                    Segment(card, CHOICES[key], self.vars[key]).pack(
                        fill=tk.X, padx=14, pady=(0, 10)
                    )
                elif kind == "file":
                    fr = tk.Frame(card, bg=C_SURFACE)
                    fr.pack(fill=tk.X, padx=14, pady=(0, 10))
                    ent = tk.Entry(
                        fr,
                        textvariable=self.vars[key],
                        bg=C_SURFACE2,
                        fg=C_TEXT,
                        insertbackground=C_TEXT,
                        relief=tk.FLAT,
                        font=("Segoe UI", 10),
                    )
                    ent.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8, padx=(0, 8))
                    _bind_clipboard(ent)
                    pick = tk.Label(
                        fr,
                        text="…",
                        bg=C_BTN,
                        fg=C_TEXT,
                        font=("Segoe UI", 11, "bold"),
                        padx=12,
                        pady=8,
                        cursor="hand2",
                        highlightthickness=1,
                        highlightbackground=C_BORDER,
                    )
                    pick.pack(side=tk.RIGHT)
                    pick.bind("<Button-1>", lambda _e, k=key: self._pick_file(k))
                else:
                    ent = tk.Entry(
                        card,
                        textvariable=self.vars[key],
                        bg=C_SURFACE2,
                        fg=C_TEXT,
                        insertbackground=C_TEXT,
                        relief=tk.FLAT,
                        font=("Segoe UI", 10),
                    )
                    ent.pack(fill=tk.X, padx=14, pady=(0, 10), ipady=8)
                    _bind_clipboard(ent)

        tk.Label(
            form,
            text=str(self.paths.root),
            bg=C_BG,
            fg=C_MUTED,
            font=("Segoe UI", 8),
            anchor="w",
            wraplength=340,
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(10, 8), padx=2)

        self._load_settings()

    def _build_log(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=C_BG)
        wrap.pack(fill=tk.BOTH, expand=True, padx=14, pady=(6, 10))
        self.log_text = tk.Text(
            wrap,
            bg=C_SURFACE,
            fg=C_TEXT,
            insertbackground=C_TEXT,
            selectbackground=C_SELECT,
            selectforeground=C_TEXT,
            relief=tk.FLAT,
            wrap=tk.WORD,
            font=("Consolas", 9),
            padx=10,
            pady=10,
            state=tk.DISABLED,
        )
        sb = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        _bind_clipboard(self.log_text)

    # ── actions ─────────────────────────────────────────────────────────

    def _do_enable(self) -> None:
        # In-process watchdog in GUI; skip second detached daemon
        self.client.enable(spawn_watchdog=False)
        self.watchdog.set_desired(True)

    def _do_disable(self) -> None:
        self.watchdog.set_desired(False)
        self.client.disable()

    def _toggle_connection(self) -> None:
        if self._busy:
            return
        if self._active:
            self._run_bg(self._do_disable, waiting="Отключение…")
        else:
            self._run_bg(self._do_enable, waiting="Подключение…")

    def _toggle_tun(self) -> None:
        if self._busy:
            return
        if self._tun:
            self._run_bg(self.client.disable_tun, waiting="Выключаю TUN…")
        else:
            self._run_bg(self.client.enable_tun, waiting="Включаю TUN…")

    def _pick_file(self, key: str) -> None:
        title = "Файл sing-box" if key == "sing_box_path" else "Приватный ключ SSH"
        path = filedialog.askopenfilename(title=title)
        if path:
            self.vars[key].set(path)

    def _load_settings(self) -> None:
        env = load_dotenv(self.paths.env_path)
        self.vars["MODE"].set(env.get("MODE", "socks") or "socks")
        self.vars["SOCKS_SCOPE"].set(env.get("SOCKS_SCOPE", "full") or "full")
        self.vars["TUN"].set(env.get("TUN", "0") or "0")
        self.vars["TUN_ELEVATE"].set(env.get("TUN_ELEVATE", "1") or "1")
        self.vars["WATCHDOG"].set(env.get("WATCHDOG", "1") or "1")
        self.vars["HTTP_BRIDGE_PORT"].set(env.get("HTTP_BRIDGE_PORT", "1088") or "1088")
        self.vars["OPS_CONTENT_SECRET"].set(env.get("OPS_CONTENT_SECRET", "") or "")
        self.vars["CORPORATE_PROXY"].set(env.get("CORPORATE_PROXY", "") or "")
        if self.paths.config_path.is_file():
            cfg = load_config(self.paths.config_path)
            ssh = cfg.get("ssh") or {}
            tun = cfg.get("tun") or {}
            self.vars["corporate_proxy"].set(str(cfg.get("corporate_proxy") or ""))
            self.vars["ssh_host"].set(str(ssh.get("host") or ""))
            self.vars["ssh_user"].set(str(ssh.get("user") or "root"))
            self.vars["ssh_port"].set(str(ssh.get("port") or 443))
            self.vars["ssh_identity"].set(str(ssh.get("identity_file") or ""))
            self.vars["ssh_socks"].set(str(ssh.get("local_socks_port") or 1080))
            self.vars["worker_base_url"].set(str(cfg.get("worker_base_url") or ""))
            bypass = cfg.get("proxy_bypass") or []
            self.vars["proxy_bypass"].set(", ".join(str(x) for x in bypass))
            self.vars["proxy_bypass_via"].set(str(cfg.get("proxy_bypass_via") or "direct"))
            self.vars["sing_box_path"].set(str(tun.get("sing_box_path") or ""))

    def _save_settings(self) -> None:
        try:
            env_vals = {
                "MODE": self.vars["MODE"].get().strip(),
                "SOCKS_SCOPE": self.vars["SOCKS_SCOPE"].get().strip(),
                "TUN": self.vars["TUN"].get().strip() or "0",
                "TUN_ELEVATE": self.vars["TUN_ELEVATE"].get().strip() or "1",
                "WATCHDOG": self.vars["WATCHDOG"].get().strip() or "1",
                "HTTP_BRIDGE_PORT": self.vars["HTTP_BRIDGE_PORT"].get().strip(),
                "OPS_CONTENT_SECRET": self.vars["OPS_CONTENT_SECRET"].get().strip(),
                "CORPORATE_PROXY": self.vars["CORPORATE_PROXY"].get().strip(),
            }
            save_dotenv(self.paths.env_path, env_vals)
            apply_dotenv(self.paths.env_path)
            if self.paths.config_path.is_file():
                cfg = load_config(self.paths.config_path)
            else:
                from desktop.config_io import default_config_template

                cfg = default_config_template()
            cfg["corporate_proxy"] = self.vars["corporate_proxy"].get().strip()
            cfg.setdefault("ssh", {})
            cfg["ssh"]["host"] = self.vars["ssh_host"].get().strip()
            cfg["ssh"]["user"] = self.vars["ssh_user"].get().strip()
            cfg["ssh"]["port"] = int(self.vars["ssh_port"].get().strip() or "443")
            cfg["ssh"]["identity_file"] = self.vars["ssh_identity"].get().strip()
            cfg["ssh"]["local_socks_port"] = int(self.vars["ssh_socks"].get().strip() or "1080")
            cfg["worker_base_url"] = self.vars["worker_base_url"].get().strip()
            raw_bypass = self.vars["proxy_bypass"].get().strip()
            cfg["proxy_bypass"] = [x.strip() for x in raw_bypass.split(",") if x.strip()]
            cfg["proxy_bypass_via"] = self.vars["proxy_bypass_via"].get().strip() or "direct"
            cfg.setdefault("tun", {})
            cfg["tun"]["sing_box_path"] = self.vars["sing_box_path"].get().strip()
            save_config(self.paths.config_path, cfg)
            self._enqueue_log("Настройки сохранены")
            messagebox.showinfo(
                "ops-content",
                "Сохранено.\nЕсли туннель был включён — выключите и включите снова.",
            )
            self._refresh_status(force=True)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("ops-content", str(exc))

    def _enqueue_log(self, msg: str) -> None:
        self.log_q.put(msg)

    def _drain_log(self) -> None:
        batch: list[str] = []
        try:
            while len(batch) < 50:
                batch.append(self.log_q.get_nowait())
        except queue.Empty:
            pass
        if batch:
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, "\n".join(batch) + "\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        if not self._closing:
            self.root.after(400 if batch else 700, self._drain_log)

    def _start_pulse(self, text: str) -> None:
        self._stop_pulse()
        self.busy_lbl.configure(text=text)
        self.status_sub.configure(text=text)

        def tick() -> None:
            if not self._busy or self._closing:
                return
            self._pulse_on = not self._pulse_on
            self.ring.configure(fg=C_ACCENT if self._pulse_on else C_ACCENT_DIM)
            self._pulse_job = self.root.after(280, tick)

        self._pulse_job = self.root.after(0, tick)

    def _stop_pulse(self) -> None:
        if self._pulse_job is not None:
            try:
                self.root.after_cancel(self._pulse_job)
            except Exception:
                pass
            self._pulse_job = None
        self._pulse_on = False

    def _set_busy(self, busy: bool, waiting: str = "Подождите…") -> None:
        self._busy = busy
        for b in (self.btn_power, self.btn_tun):
            b._disabled = busy  # type: ignore[attr-defined]
            b.configure(cursor="watch" if busy else "hand2")
        if busy:
            self._start_pulse(waiting)
        else:
            self._stop_pulse()
            self.busy_lbl.configure(text="")

    def _run_bg(self, fn: Callable[[], None], waiting: str = "Подождите…") -> None:
        if self._busy:
            return

        def work() -> None:
            self.root.after(0, lambda: self._set_busy(True, waiting))
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                self._enqueue_log(f"ошибка: {exc}")
                self.root.after(0, lambda: messagebox.showerror("ops-content", str(exc)))
            finally:
                self.root.after(0, lambda: self._set_busy(False))
                self.root.after(0, lambda: self._refresh_status(force=True))

        threading.Thread(target=work, daemon=True).start()

    def _probe(self) -> None:
        try:
            cfg = self.client.config()
            host = str((cfg.get("ssh") or {}).get("host") or "")
            port = int((cfg.get("ssh") or {}).get("port") or 443)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("ops-content", str(exc))
            return
        if not host or "YOUR_VPS" in host:
            messagebox.showwarning("ops-content", "Укажите адрес сервера в настройках")
            self._show_page("settings")
            return
        self._run_bg(lambda: self.client.probe(host, port), waiting="Проверка…")

    def _mode_label(self, mode: str) -> str:
        return {"socks": "Туннель", "vps": "VPS"}.get(mode, mode or "—")

    def _scope_label(self, scope: str) -> str:
        return {"full": "Всё", "github": "GitHub"}.get(scope, scope or "—")

    def _apply_status_ui(self, st: dict[str, Any]) -> None:
        ssh = bool(st.get("ssh_running"))
        tun = bool(st.get("tun_running"))
        socks = bool(st.get("socks_listening"))
        active = bool(st.get("active")) or ssh
        self._active = active
        self._tun = tun

        mode = self._mode_label(str(st.get("mode") or ""))
        scope = self._scope_label(str(st.get("socks_scope") or ""))
        target = str(st.get("ssh_target") or "—")
        sig = (
            f"{ssh}|{tun}|{socks}|{active}|{mode}|{scope}|{target}|"
            f"{(st.get('state') or {}).get('mode')}|{self.watchdog.desired}"
        )
        if sig == self._last_status_sig or self._busy:
            return
        self._last_status_sig = sig

        if (tun or ssh or self.watchdog.desired) and not socks and st.get("mode") != "vps":
            title, sub, color = "Сбой", "SOCKS недоступен — переподключение…", C_DANGER
            self._recolor_btn(self.btn_power, danger=True, text="Отключить")
        elif tun and ssh and socks:
            title, sub, color = "Защищено", "Туннель и TUN активны", C_ACCENT
            self._recolor_btn(self.btn_power, danger=True, text="Отключить")
        elif tun and socks:
            title, sub, color = "TUN", "TUN через SOCKS", C_WARN
            self._recolor_btn(self.btn_power, danger=True, text="Отключить")
        elif tun:
            title, sub, color = "TUN", "Без SSH-туннеля", C_WARN
            self._recolor_btn(self.btn_power, danger=True, text="Отключить")
        elif ssh and socks:
            title, sub, color = "Подключено", "Туннель активен", C_OK
            self._recolor_btn(self.btn_power, danger=True, text="Отключить")
        elif ssh:
            title, sub, color = "Подключено", "SSH есть, SOCKS закрыт", C_WARN
            self._recolor_btn(self.btn_power, danger=True, text="Отключить")
        elif (st.get("state") or {}).get("mode") == "relay":
            title, sub, color = "Relay", "Режим relay", C_WARN
            self._recolor_btn(self.btn_power, danger=True, text="Отключить")
        elif active:
            title, sub, color = "Включено", "Активно", C_OK
            self._recolor_btn(self.btn_power, danger=True, text="Отключить")
        else:
            title, sub, color = "Отключено", "Нажмите «Подключить»", C_MUTED
            self._recolor_btn(self.btn_power, primary=True, text="Подключить")

        self.status_title.configure(text=title)
        self.status_sub.configure(text=sub)
        self.ring.configure(fg=color)
        self.btn_tun.configure(
            text="TUN выкл" if tun else "TUN вкл",
            fg=C_ACCENT if tun else C_TEXT,
            highlightbackground=C_ACCENT if tun else C_BORDER,
        )
        self.meta_rows["mode"].configure(text=mode)
        self.meta_rows["scope"].configure(text=scope)
        self.meta_rows["target"].configure(text=target)

    def _refresh_status(self, force: bool = False) -> None:
        if self._closing:
            return
        if self._status_busy:
            self.root.after(1000, self._refresh_status)
            return

        def work() -> None:
            self._status_busy = True
            try:
                st = self.client.status(include_git=False)
            except Exception as exc:  # noqa: BLE001
                def err() -> None:
                    if not self._busy:
                        self.status_title.configure(text="Ошибка")
                        self.status_sub.configure(text=str(exc)[:80])
                        self.ring.configure(fg=C_DANGER)

                self.root.after(0, err)
            else:
                def ok() -> None:
                    if force:
                        self._last_status_sig = ""
                    self._apply_status_ui(st)

                self.root.after(0, ok)
            finally:
                self._status_busy = False
                if not self._closing:
                    delay = 5000 if self._page == "home" and not self._busy else 10000
                    self.root.after(delay, self._refresh_status)

        threading.Thread(target=work, daemon=True).start()

    def _on_close(self) -> None:
        self.root.withdraw()

    def _quit_app(self) -> None:
        self._closing = True
        self._stop_pulse()
        self.watchdog.set_desired(False)
        self.watchdog.stop()
        try:
            st = self.client.status()
            if st.get("ssh_running") or st.get("bridge_running") or st.get("tun_running"):
                self.client.disable()
        except Exception:  # noqa: BLE001
            try:
                self.client.stop_http_bridge()
            except Exception:  # noqa: BLE001
                pass
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:  # noqa: BLE001
                pass
        self.root.after(0, self.root.destroy)

    def _show_window(self) -> None:
        self.root.after(0, self._deiconify)

    def _deiconify(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_icon_image(self):
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((8, 8, 56, 56), fill=(45, 212, 168, 255))
        d.rectangle((28, 18, 36, 46), fill=(16, 20, 26, 255))
        d.rectangle((20, 28, 44, 36), fill=(16, 20, 26, 255))
        return img

    def _start_tray(self) -> None:
        try:
            import pystray
            from pystray import MenuItem as Item
        except ImportError:
            self._enqueue_log("трей недоступен (нужен pystray)")
            return

        menu = pystray.Menu(
            Item("Открыть", lambda _i, _j: self._show_window(), default=True),
            Item("Подключить", lambda _i, _j: self.root.after(0, lambda: self._run_bg(self._do_enable, "Подключение…"))),
            Item("Отключить", lambda _i, _j: self.root.after(0, lambda: self._run_bg(self._do_disable, "Отключение…"))),
            Item("TUN вкл", lambda _i, _j: self.root.after(0, lambda: self._run_bg(self.client.enable_tun, "Включаю TUN…"))),
            Item("TUN выкл", lambda _i, _j: self.root.after(0, lambda: self._run_bg(self.client.disable_tun, "Выключаю TUN…"))),
            Item("Выход", lambda _i, _j: self.root.after(0, self._quit_app)),
        )
        self._tray = pystray.Icon("ops-content", self._tray_icon_image(), "ops-content", menu)
        self._tray_thread = threading.Thread(target=self._tray.run, daemon=True)
        self._tray_thread.start()

    def _tray_notify(self, title: str, message: str) -> None:
        def show() -> None:
            tray = self._tray
            if tray is None:
                return
            try:
                tray.notify(message, title)
            except Exception:  # noqa: BLE001
                pass

        try:
            self.root.after(0, show)
        except Exception:  # noqa: BLE001
            show()

    def _start_watchdog(self) -> None:
        if self.client.watchdog_daemon_alive():
            # CLI `on` already spawned a background watchdog
            if infer_desired_on(self.client) or self._active:
                self.watchdog.set_desired(True)
            self._enqueue_log("watchdog: фоновый процесс уже запущен (после on)")
            return
        if infer_desired_on(self.client) or self._active:
            self.watchdog.set_desired(True)
        self.watchdog.start()

    def run(self) -> None:
        self.root.mainloop()


def run_gui() -> None:
    App().run()


if __name__ == "__main__":
    run_gui()
