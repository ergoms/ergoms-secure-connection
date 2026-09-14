"""Single mapping from OpsClient.status() dict to UI presentation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

C_MUTED = "#8b95a8"
C_ACCENT = "#2dd4a8"
C_OK = "#2dd4a8"
C_WARN = "#e6c07b"
C_DANGER = "#f07178"


def scope_label(scope: str) -> str:
    return {"full": "Всё", "github": "GitHub"}.get(scope, scope or "—")


@dataclass
class StatusView:
    active: bool
    tun: bool
    kill_switch_on: bool
    singbox_up: bool
    socks_up: bool
    http_up: bool
    pac_up: bool
    watchdog_up: bool
    reverse_ssh_up: bool
    reverse_ssh_port: int
    socks_port: int
    http_port: int
    pac_port: int
    server_target: str
    scope: str
    title: str
    subtitle: str
    color: str
    power_text: str
    tun_button_text: str
    toast: str | None = None
    signature: str = ""


def present_status(
    st: dict[str, Any],
    *,
    config_ready: bool,
    corporate: bool = False,
) -> StatusView:
    del corporate
    singbox = bool(st.get("singbox_running"))
    tun = bool(st.get("tun_running"))
    socks_up = bool(st.get("socks_up")) if "socks_up" in st else singbox
    http_up = bool(st.get("http_up")) if "http_up" in st else singbox
    pac_up = bool(st.get("pac_up")) if "pac_up" in st else singbox
    active = bool(singbox or tun)
    scope = scope_label(str(st.get("socks_scope") or ""))
    target = str(st.get("server_target") or st.get("ssh_target") or "—")
    ks_on = bool(st.get("kill_switch_applied") or (active and st.get("kill_switch")))
    probe_err = str(st.get("exit_probe_error") or "")
    probe_hint = str(st.get("exit_probe_hint") or "")
    socks_port = int(st.get("socks_port") or 1080)
    toast: str | None = None
    if singbox and socks_up and probe_err:
        if probe_hint == "need-hy2":
            title, sub, color = (
                "Нет выхода",
                "Reality не дал выход. Интернет закрыт — Hy2/AWG или отключите VPN",
                C_DANGER,
            )
        elif probe_hint == "hy2-udp":
            title, sub, color = (
                "Нет выхода",
                "UDP не дошёл. Интернет закрыт — проверьте порт на VPS или отключите VPN",
                C_DANGER,
            )
        else:
            title, sub, color = (
                "Нет выхода",
                "Туннель без выхода. Интернет закрыт — отключите VPN, чтобы снять блок",
                C_DANGER,
            )
        power = "Отключить"
        toast = sub
    elif singbox and tun and socks_up:
        title, sub, color, power = "Защищено", "", C_ACCENT, "Отключить"
    elif singbox and not socks_up:
        title, sub, color, power = (
            "Сбой",
            f"процесс есть, SOCKS :{socks_port} молчит — смотрите журнал",
            C_DANGER,
            "Отключить",
        )
    elif singbox:
        title, sub, color, power = "Подключено", "", C_OK, "Отключить"
    elif tun:
        title, sub, color, power = "TUN", "Без VLESS", C_WARN, "Отключить"
    elif ks_on:
        title, sub, color, power = (
            "Нет сети",
            "Интернет закрыт: туннель упал. Отключите VPN, чтобы снять блок",
            C_WARN,
            "Отключить",
        )
    elif not config_ready:
        title, sub, color, power = "Нет конфига", "Загрузите конфиг", C_MUTED, "Подключить"
    else:
        title, sub, color, power = "Отключено", "", C_MUTED, "Подключить"
    sig = (
        f"{singbox}|{tun}|{active}|{socks_up}|{http_up}|{pac_up}|{scope}|{target}"
        f"|{st.get('watchdog_running')}|{st.get('reverse_ssh_running')}"
        f"|{st.get('reverse_ssh_listen')}|{ks_on}|{probe_err}|{probe_hint}"
    )
    return StatusView(
        active=active,
        tun=tun,
        kill_switch_on=ks_on,
        singbox_up=singbox,
        socks_up=socks_up,
        http_up=http_up,
        pac_up=pac_up,
        watchdog_up=bool(st.get("watchdog_running")),
        reverse_ssh_up=bool(st.get("reverse_ssh_running")),
        reverse_ssh_port=int(st.get("reverse_ssh_listen") or 2222),
        socks_port=socks_port,
        http_port=int(st.get("http_port") or 1088),
        pac_port=int(st.get("pac_port") or 1089),
        server_target=target,
        scope=scope,
        title=title,
        subtitle=sub,
        color=color,
        power_text=power,
        tun_button_text="TUN выкл" if tun else "TUN вкл",
        toast=toast,
        signature=sig,
    )
