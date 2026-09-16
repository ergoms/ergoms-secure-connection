"""Process spawn strategies: normal, Windows UAC, Linux pkexec/sudo."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from desktop import procutil

LogFn = Callable[[str], None]
FindPid = Callable[[], int | None]


class _Launchable(Protocol):
    def _launch(
        self,
        exe: Path,
        *,
        elevate: bool,
        prelude_cmds: list[str] | None = None,
        postlude_cmds: list[str] | None = None,
    ) -> int | None: ...


def launch(
    mgr: _Launchable,
    exe: Path,
    *,
    elevate: bool,
    prelude_cmds: list[str] | None = None,
    postlude_cmds: list[str] | None = None,
) -> int | None:
    return mgr._launch(
        exe,
        elevate=elevate,
        prelude_cmds=prelude_cmds,
        postlude_cmds=postlude_cmds,
    )


class NormalSpawn:
    def start(
        self,
        args: list[str],
        log_path: Path,
        postlude: list[str],
        run_postlude: Callable[[list[str]], None],
    ) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            creationflags=procutil.creationflags(),
        )
        if postlude:
            threading.Thread(
                target=run_postlude, args=(list(postlude),), daemon=True
            ).start()
        return int(proc.pid)


class WinElevatedSpawn:
    def start(
        self,
        exe: Path,
        config: Path,
        var_dir: Path,
        prelude: list[str],
        postlude: list[str],
        find_pid: FindPid,
    ) -> int | None:
        import ctypes

        if prelude or postlude:
            wrapper = var_dir / "sing-box-elevated.cmd"
            lines = ["@echo off"]
            for cmd in prelude:
                lines.append(f"{cmd} 2>nul")
            if postlude:
                delayed = "timeout /t 2 /nobreak >nul"
                for cmd in postlude:
                    delayed += f" & {cmd} 2>nul"
                lines.append(f'start "ergoms-pin" /b cmd /c "{delayed}"')
            lines.append(f'"{exe}" run -c "{config}"')
            wrapper.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
            file, params, cwd = str(wrapper), "", str(var_dir)
        else:
            file, params, cwd = str(exe), f'run -c "{config}"', str(exe.parent)
        rc = int(
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", file, params, cwd, 0
            )
        )
        if rc <= 32:
            raise RuntimeError(
                f"UAC для sing-box mode не удался (код {rc}). "
                "Запустите от администратора или TUN=0."
            )
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            found = find_pid()
            if found:
                return found
            time.sleep(0.05)
        return None


class LinuxElevatedSpawn:
    def start(
        self,
        args: list[str],
        log_path: Path,
        prelude: list[str],
        find_pid: FindPid,
        log: LogFn,
    ) -> int | None:
        import shlex

        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        if prelude:
            inner = " ; ".join(prelude) + " ; exec " + " ".join(
                shlex.quote(a) for a in args
            )
            launched = ["sh", "-c", inner]
        else:
            launched = args
        for wrapper in (
            ["pkexec", *launched],
            ["sudo", "-n", *launched],
            ["sudo", *launched],
        ):
            try:
                proc = subprocess.Popen(
                    wrapper,
                    stdin=subprocess.DEVNULL,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                )
                time.sleep(0.5)
                if proc.poll() is None or find_pid():
                    log(f"singbox mode via {wrapper[0]}")
                    return proc.pid if proc.poll() is None else find_pid()
            except FileNotFoundError:
                continue
        raise RuntimeError(
            "Не удалось запустить sing-box mode с root (pkexec/sudo). "
            "Или TUN=0, или: sudo python -m desktop on"
        )


def needs_win_elevation(*, elevate: bool) -> bool:
    return sys.platform == "win32" and elevate and not procutil.is_admin()


def needs_linux_elevation(*, elevate: bool) -> bool:
    return (
        sys.platform != "win32"
        and elevate
        and hasattr(os, "geteuid")
        and os.geteuid() != 0
    )
