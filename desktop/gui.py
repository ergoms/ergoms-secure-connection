"""Tkinter GUI + system tray for ops-content."""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from desktop.client import OpsClient
from desktop.config_io import (
    apply_dotenv,
    load_config,
    load_dotenv,
    save_config,
    save_dotenv,
)
from desktop.paths import Paths, bundle_dir


BG = "#1e1e1e"
BG2 = "#2a2a2a"
FG = "#e8e8e8"
MUTED = "#9a9a9a"
ACCENT = "#3d7a5a"
DANGER = "#8b4a4a"
OK = "#5a9a6a"


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

        self.root = tk.Tk()
        self.root.title("ops-content")
        self.root.geometry("720x560")
        self.root.minsize(560, 420)
        self.root.configure(bg=BG)
        # Same backend as CLI (Linux/Windows); GUI is only a front-end
        self._enqueue_log(f"platform={sys.platform} — команды те же, что у CLI/EXE")
        self._set_icon()
        self._style()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(200, self._drain_log)
        self.root.after(500, self._refresh_status)
        self.root.after(800, self._start_tray)
        # Ensure config exists
        if not self.paths.config_path.is_file() or not self.paths.env_path.is_file():
            try:
                self.client.init()
                self._enqueue_log("Инициализация: созданы config.json / .env при необходимости")
            except Exception as exc:  # noqa: BLE001
                self._enqueue_log(f"init: {exc}")

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
        style.configure(".", background=BG, foreground=FG, fieldbackground=BG2)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("TNotebook", background=BG)
        style.configure("TNotebook.Tab", background=BG2, foreground=FG, padding=[10, 4])
        style.map("TNotebook.Tab", background=[("selected", "#3a3a3a")])
        style.configure("TButton", background=BG2, foreground=FG, padding=6)
        style.map("TButton", background=[("active", "#3a3a3a")])
        style.configure("TEntry", fieldbackground=BG2, foreground=FG)
        style.configure("TCombobox", fieldbackground=BG2, foreground=FG)
        style.configure("Status.TLabel", font=("Segoe UI", 11, "bold"))

    def _build(self) -> None:
        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.tab_main = ttk.Frame(nb)
        self.tab_settings = ttk.Frame(nb)
        nb.add(self.tab_main, text="Управление")
        nb.add(self.tab_settings, text="Настройки")
        self._build_main(self.tab_main)
        self._build_settings(self.tab_settings)

    def _build_main(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, pady=(4, 8))
        self.status_dot = tk.Canvas(top, width=16, height=16, bg=BG, highlightthickness=0)
        self.status_dot.pack(side=tk.LEFT, padx=(0, 8))
        self._dot_id = self.status_dot.create_oval(2, 2, 14, 14, fill=MUTED, outline="")
        self.status_lbl = ttk.Label(top, text="Статус: …", style="Status.TLabel")
        self.status_lbl.pack(side=tk.LEFT)
        self.meta_lbl = ttk.Label(top, text="", foreground=MUTED)
        self.meta_lbl.pack(side=tk.RIGHT)

        btns = ttk.Frame(parent)
        btns.pack(fill=tk.X, pady=4)
        self.btn_on = ttk.Button(btns, text="Включить", command=lambda: self._run_bg(self.client.enable))
        self.btn_on.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_off = ttk.Button(btns, text="Выключить", command=lambda: self._run_bg(self.client.disable))
        self.btn_off.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btns, text="Обновить", command=self._refresh_status).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btns, text="Probe", command=self._probe).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btns, text="Тест", command=lambda: self._run_bg(self.client.test_bypass)).pack(
            side=tk.LEFT
        )

        tun_row = ttk.Frame(parent)
        tun_row.pack(fill=tk.X, pady=(6, 4))
        ttk.Label(tun_row, text="TUN (как VPN через SOCKS):", foreground=MUTED).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        self.btn_tun_on = ttk.Button(
            tun_row, text="TUN вкл", command=lambda: self._run_bg(self.client.enable_tun)
        )
        self.btn_tun_on.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_tun_off = ttk.Button(
            tun_row, text="TUN выкл", command=lambda: self._run_bg(self.client.disable_tun)
        )
        self.btn_tun_off.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            tun_row,
            text="Скачать sing-box",
            command=lambda: self._run_bg(self.client.download_sing_box),
        ).pack(side=tk.LEFT)

        ttk.Label(parent, text="Журнал", foreground=MUTED).pack(anchor=tk.W, pady=(10, 2))
        self.log_text = tk.Text(
            parent,
            height=18,
            bg=BG2,
            fg=FG,
            insertbackground=FG,
            relief=tk.FLAT,
            wrap=tk.WORD,
            font=("Consolas", 9),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(state=tk.DISABLED)

    def _build_settings(self, parent: ttk.Frame) -> None:
        form = ttk.Frame(parent)
        form.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self.vars: dict[str, tk.Variable] = {
            "MODE": tk.StringVar(value="socks"),
            "SOCKS_SCOPE": tk.StringVar(value="full"),
            "TUN": tk.StringVar(value="0"),
            "TUN_ELEVATE": tk.StringVar(value="1"),
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

        rows: list[tuple[str, str, Any]] = [
            ("MODE (.env)", "MODE", ("socks", "vps")),
            ("SOCKS_SCOPE (.env)", "SOCKS_SCOPE", ("full", "github")),
            ("TUN (.env)", "TUN", ("0", "1")),
            ("TUN_ELEVATE (.env)", "TUN_ELEVATE", ("0", "1")),
            ("HTTP_BRIDGE_PORT (.env)", "HTTP_BRIDGE_PORT", None),
            ("OPS_CONTENT_SECRET (.env)", "OPS_CONTENT_SECRET", None),
            ("CORPORATE_PROXY (.env, опц.)", "CORPORATE_PROXY", None),
            ("corporate_proxy (config)", "corporate_proxy", None),
            ("SSH host (config)", "ssh_host", None),
            ("SSH user (config)", "ssh_user", None),
            ("SSH port (config)", "ssh_port", None),
            ("SSH key (config)", "ssh_identity", "file"),
            ("SOCKS port (config)", "ssh_socks", None),
            ("worker_base_url (config)", "worker_base_url", None),
            ("proxy_bypass (config)", "proxy_bypass", None),
            ("proxy_bypass_via (config)", "proxy_bypass_via", ("direct", "corporate")),
            ("sing-box path (config)", "sing_box_path", "file"),
        ]

        for i, (label, key, kind) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky=tk.W, pady=3, padx=(0, 8))
            if kind == "file":
                fr = ttk.Frame(form)
                fr.grid(row=i, column=1, sticky=tk.EW, pady=3)
                ttk.Entry(fr, textvariable=self.vars[key], width=48).pack(
                    side=tk.LEFT, fill=tk.X, expand=True
                )
                ttk.Button(
                    fr, text="…", width=3, command=lambda k=key: self._pick_file(k)
                ).pack(side=tk.LEFT, padx=4)
            elif isinstance(kind, tuple):
                cb = ttk.Combobox(
                    form, textvariable=self.vars[key], values=kind, state="readonly", width=46
                )
                cb.grid(row=i, column=1, sticky=tk.EW, pady=3)
            else:
                ttk.Entry(form, textvariable=self.vars[key], width=50).grid(
                    row=i, column=1, sticky=tk.EW, pady=3
                )
        form.columnconfigure(1, weight=1)

        bar = ttk.Frame(parent)
        bar.pack(fill=tk.X, pady=8)
        ttk.Button(bar, text="Загрузить", command=self._load_settings).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bar, text="Сохранить", command=self._save_settings).pack(side=tk.LEFT)

        self._load_settings()

    def _pick_file(self, key: str) -> None:
        title = "sing-box.exe" if key == "sing_box_path" else "SSH private key"
        path = filedialog.askopenfilename(title=title)
        if path:
            self.vars[key].set(path)

    def _load_settings(self) -> None:
        env = load_dotenv(self.paths.env_path)
        self.vars["MODE"].set(env.get("MODE", "socks") or "socks")
        self.vars["SOCKS_SCOPE"].set(env.get("SOCKS_SCOPE", "full") or "full")
        self.vars["TUN"].set(env.get("TUN", "0") or "0")
        self.vars["TUN_ELEVATE"].set(env.get("TUN_ELEVATE", "1") or "1")
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
            self._enqueue_log("Сохранено: .env + config.json")
            messagebox.showinfo(
                "ops-content",
                "Сохранено в .env и config.json.\nПерезапустите туннель (Выкл → Вкл).",
            )
            self._refresh_status()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("ops-content", str(exc))

    def _enqueue_log(self, msg: str) -> None:
        self.log_q.put(msg)

    def _drain_log(self) -> None:
        try:
            while True:
                msg = self.log_q.get_nowait()
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, msg + "\n")
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        if not self._closing:
            self.root.after(200, self._drain_log)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for b in (self.btn_on, self.btn_off, self.btn_tun_on, self.btn_tun_off):
            b.configure(state=state)

    def _run_bg(self, fn: Callable[[], None]) -> None:
        if self._busy:
            return

        def work() -> None:
            self.root.after(0, lambda: self._set_busy(True))
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                self._enqueue_log(f"ERROR: {exc}")
                self.root.after(0, lambda: messagebox.showerror("ops-content", str(exc)))
            finally:
                self.root.after(0, lambda: self._set_busy(False))
                self.root.after(0, self._refresh_status)

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
            messagebox.showwarning("ops-content", "Укажите ssh.host в настройках")
            return
        self._run_bg(lambda: self.client.probe(host, port))

    def _refresh_status(self) -> None:
        try:
            st = self.client.status()
        except Exception as exc:  # noqa: BLE001
            self.status_lbl.configure(text=f"Ошибка: {exc}")
            return
        active = bool(st.get("active"))
        color = OK if active else MUTED
        if st.get("tun_running"):
            color = ACCENT
        self.status_dot.itemconfigure(self._dot_id, fill=color)
        label = "Выключено"
        if st.get("tun_running") and st.get("ssh_running"):
            label = "Туннель + TUN"
        elif st.get("tun_running"):
            label = "TUN активен"
        elif st.get("ssh_running"):
            label = "Туннель активен"
        elif (st.get("state") or {}).get("mode") == "relay":
            label = "Relay активен"
        elif active:
            label = "Включено"
        self.status_lbl.configure(text=f"Статус: {label}")
        meta = f"{st.get('mode')} | {st.get('ssh_target') or '—'}"
        if st.get("socks_scope"):
            meta = f"{st.get('mode')}/{st.get('socks_scope')} | {st.get('ssh_target') or '—'}"
        if st.get("tun_running"):
            meta += " | TUN"
        self.meta_lbl.configure(text=meta)
        if not self._closing:
            self.root.after(3000, self._refresh_status)

    def _on_close(self) -> None:
        self.root.withdraw()

    def _quit_app(self) -> None:
        self._closing = True
        try:
            st = self.client.status()
            if st.get("ssh_running") or st.get("bridge_running"):
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
        d.ellipse((8, 8, 56, 56), fill=(61, 122, 90, 255))
        d.rectangle((28, 18, 36, 46), fill=(30, 30, 30, 255))
        d.rectangle((20, 28, 44, 36), fill=(30, 30, 30, 255))
        return img

    def _start_tray(self) -> None:
        try:
            import pystray
            from pystray import MenuItem as Item
        except ImportError:
            self._enqueue_log("pystray не установлен — трей отключён (pip install pystray pillow)")
            return

        def on_on(icon, item):  # noqa: ARG001
            self.root.after(0, lambda: self._run_bg(self.client.enable))

        def on_off(icon, item):  # noqa: ARG001
            self.root.after(0, lambda: self._run_bg(self.client.disable))

        def on_open(icon, item):  # noqa: ARG001
            self._show_window()

        def on_quit(icon, item):  # noqa: ARG001
            self.root.after(0, self._quit_app)

        def on_tun_on(icon, item):  # noqa: ARG001
            self.root.after(0, lambda: self._run_bg(self.client.enable_tun))

        def on_tun_off(icon, item):  # noqa: ARG001
            self.root.after(0, lambda: self._run_bg(self.client.disable_tun))

        menu = pystray.Menu(
            Item("Открыть", on_open, default=True),
            Item("Включить", on_on),
            Item("Выключить", on_off),
            Item("TUN вкл", on_tun_on),
            Item("TUN выкл", on_tun_off),
            Item("Выход", on_quit),
        )
        self._tray = pystray.Icon("ops-content", self._tray_icon_image(), "ops-content", menu)

        def run_tray() -> None:
            assert self._tray is not None
            self._tray.run()

        self._tray_thread = threading.Thread(target=run_tray, daemon=True)
        self._tray_thread.start()

    def run(self) -> None:
        self.root.mainloop()


def run_gui() -> None:
    App().run()


if __name__ == "__main__":
    run_gui()
