"""Single vocabulary for connect / disconnect / reconnect / TUN."""

from __future__ import annotations

from enum import StrEnum


class Action(StrEnum):
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    RECONNECT = "reconnect"
    TUN_ON = "tun-on"
    TUN_OFF = "tun-off"

    @property
    def cli_command(self) -> str:
        return {
            Action.CONNECT: "on",
            Action.DISCONNECT: "off",
            Action.RECONNECT: "on",
            Action.TUN_ON: "tun-on",
            Action.TUN_OFF: "tun-off",
        }[self]

    @property
    def cli_flag(self) -> str:
        return {
            Action.CONNECT: "--connect",
            Action.DISCONNECT: "--disconnect",
            Action.RECONNECT: "--connect",
            Action.TUN_ON: "--tun-on",
            Action.TUN_OFF: "--tun-off",
        }[self]

    @property
    def resume(self) -> str:
        return {
            Action.CONNECT: "on",
            Action.DISCONNECT: "off",
            Action.RECONNECT: "on",
            Action.TUN_ON: "tun-on",
            Action.TUN_OFF: "tun-off",
        }[self]

    @property
    def elevation_key(self) -> str:
        return {
            Action.CONNECT: "on",
            Action.DISCONNECT: "off",
            Action.RECONNECT: "on",
            Action.TUN_ON: "tun-on",
            Action.TUN_OFF: "tun-off",
        }[self]

    @property
    def ru_name(self) -> str:
        return {
            Action.CONNECT: "подключение",
            Action.DISCONNECT: "отключение",
            Action.RECONNECT: "переподключение",
            Action.TUN_ON: "включение TUN",
            Action.TUN_OFF: "выключение TUN",
        }[self]

    @property
    def waiting_text(self) -> str:
        return {
            Action.CONNECT: "Подключение…",
            Action.DISCONNECT: "Отключение…",
            Action.RECONNECT: "Переподключение…",
            Action.TUN_ON: "Включаю TUN…",
            Action.TUN_OFF: "Выключаю TUN…",
        }[self]

    @property
    def busy_intent(self) -> str:
        """GUI wait: on = until tunnel/TUN ready, off = until processes gone."""
        return {
            Action.CONNECT: "on",
            Action.DISCONNECT: "off",
            Action.RECONNECT: "on",
            Action.TUN_ON: "on",
            Action.TUN_OFF: "off",
        }[self]

    @property
    def client_method(self) -> str:
        return {
            Action.CONNECT: "enable",
            Action.DISCONNECT: "disable",
            Action.RECONNECT: "reconnect",
            Action.TUN_ON: "enable_tun",
            Action.TUN_OFF: "disable_tun",
        }[self]


CLI_TO_ACTION: dict[str, Action] = {
    "on": Action.CONNECT,
    "start": Action.CONNECT,
    "off": Action.DISCONNECT,
    "stop": Action.DISCONNECT,
    "tun-on": Action.TUN_ON,
    "tun-off": Action.TUN_OFF,
}

RESUME_FLAGS: dict[str, Action] = {
    "--connect": Action.CONNECT,
    "--disconnect": Action.DISCONNECT,
    "--tun-on": Action.TUN_ON,
    "--tun-off": Action.TUN_OFF,
}

RESUME_ENV: dict[str, Action] = {
    "on": Action.CONNECT,
    "off": Action.DISCONNECT,
    "tun-on": Action.TUN_ON,
    "tun-off": Action.TUN_OFF,
}


def action_from_cli(cmd: str) -> Action | None:
    return CLI_TO_ACTION.get((cmd or "").lower())


def action_from_flag(flag: str) -> Action | None:
    return RESUME_FLAGS.get(flag)


def action_from_resume(value: str) -> Action | None:
    return RESUME_ENV.get((value or "").lower())
