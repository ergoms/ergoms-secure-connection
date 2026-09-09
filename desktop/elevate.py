"""Elevate the GUI once at startup (Windows) and keep a scheduled task for later."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from desktop import procutil
from desktop.branding import APP_NAME, ENV_NO_ELEVATE, env
from desktop.paths import data_root, is_frozen

TASK_DEMAND = APP_NAME
TASK_AUTOSTART = f"{APP_NAME} (автозапуск)"


def _skip_elevate() -> bool:
    return env(ENV_NO_ELEVATE).lower() in {"1", "true", "yes"}


def _user_id() -> str:
    domain = (os.environ.get("USERDOMAIN") or "").strip()
    user = (os.environ.get("USERNAME") or "").strip()
    if domain and user:
        return f"{domain}\\{user}"
    return user or os.environ.get("USER", "")


def _gui_exe_and_cwd() -> tuple[str, str, str]:
    """Return (command, extra_args_without_autostart, working_directory)."""
    if is_frozen():
        exe = str(Path(sys.executable).resolve())
        return exe, "", str(Path(exe).parent)
    exe = Path(sys.executable).resolve()
    if exe.name.lower() == "python.exe":
        pw = exe.with_name("pythonw.exe")
        if pw.is_file():
            exe = pw
    extra = "-m desktop gui"
    cwd = str(Path(__file__).resolve().parent.parent)
    return str(exe), extra, cwd


def _task_xml(*, autostart: bool) -> str:
    command, extra, cwd = _gui_exe_and_cwd()
    user = _user_id()
    args = extra
    if autostart:
        args = f"{extra} --autostart".strip() if extra else "--autostart"
    trigger = ""
    if autostart and user:
        trigger = (
            "  <Triggers>\n"
            "    <LogonTrigger>\n"
            "      <Enabled>true</Enabled>\n"
            f"      <UserId>{escape(user)}</UserId>\n"
            "    </LogonTrigger>\n"
            "  </Triggers>\n"
        )
    else:
        trigger = "  <Triggers />\n"
    principal_user = f"      <UserId>{escape(user)}</UserId>\n" if user else ""
    arguments = (
        f"      <Arguments>{escape(args)}</Arguments>\n" if args else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        f"    <URI>\\{escape(TASK_AUTOSTART if autostart else TASK_DEMAND)}</URI>\n"
        "  </RegistrationInfo>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"{principal_user}"
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>HighestAvailable</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <AllowHardTerminate>false</AllowHardTerminate>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n"
        "    <IdleSettings>\n"
        "      <StopOnIdleEnd>false</StopOnIdleEnd>\n"
        "      <RestartOnIdle>false</RestartOnIdle>\n"
        "    </IdleSettings>\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
        "    <Enabled>true</Enabled>\n"
        "    <Hidden>false</Hidden>\n"
        "    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n"
        "    <WakeToRun>false</WakeToRun>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <Priority>7</Priority>\n"
        "  </Settings>\n"
        f"{trigger}"
        "  <Actions Context=\"Author\">\n"
        "    <Exec>\n"
        f"      <Command>{escape(command)}</Command>\n"
        f"{arguments}"
        f"      <WorkingDirectory>{escape(cwd)}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def task_exists(name: str) -> bool:
    if sys.platform != "win32":
        return False
    r = procutil.run(["schtasks", "/Query", "/TN", name], timeout=8)
    return r.returncode == 0


def task_enabled(name: str) -> bool:
    if not task_exists(name):
        return False
    r = procutil.run(["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"], timeout=8)
    for line in (r.stdout or "").splitlines():
        low = line.lower()
        if low.startswith("status:") or low.startswith("состояние:"):
            return "disabled" not in low and "отключ" not in low
    return r.returncode == 0


def task_command_matches(name: str) -> bool:
    if not task_exists(name):
        return False
    r = procutil.run(["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"], timeout=8)
    want = Path(_gui_exe_and_cwd()[0]).resolve()
    want_s = str(want).lower()
    for line in (r.stdout or "").splitlines():
        low = line.lower()
        if "task to run" in low or "выполняемая задача" in low:
            if want_s in low.replace('"', ""):
                return True
    return want_s in (r.stdout or "").lower().replace('"', "")


def delete_task(name: str) -> None:
    if sys.platform != "win32":
        return
    procutil.run(["schtasks", "/Delete", "/TN", name, "/F"], timeout=12)


def create_task(name: str, *, autostart: bool) -> bool:
    if sys.platform != "win32":
        return False
    xml = _task_xml(autostart=autostart)
    tmp = Path(tempfile.gettempdir()) / f"ergoms-task-{os.getpid()}-{int(autostart)}.xml"
    try:
        tmp.write_text(xml, encoding="utf-16")
        r = procutil.run(
            ["schtasks", "/Create", "/TN", name, "/XML", str(tmp), "/F"],
            timeout=20,
        )
        return r.returncode == 0
    finally:
        tmp.unlink(missing_ok=True)


def set_task_enabled(name: str, on: bool) -> bool:
    if sys.platform != "win32":
        return False
    if on:
        if not task_exists(name) or not task_command_matches(name):
            if not create_task(name, autostart=True):
                return False
        r = procutil.run(["schtasks", "/Change", "/TN", name, "/ENABLE"], timeout=12)
        return r.returncode == 0 or task_enabled(name)
    if not task_exists(name):
        return True
    r = procutil.run(["schtasks", "/Change", "/TN", name, "/DISABLE"], timeout=12)
    return r.returncode == 0 or not task_enabled(name)


def run_task(name: str) -> bool:
    if sys.platform != "win32":
        return False
    r = procutil.run(["schtasks", "/Run", "/TN", name], timeout=15)
    return r.returncode == 0


def ensure_scheduled_tasks() -> None:
    """Create/update demand + autostart task definitions (caller must be admin)."""
    if sys.platform != "win32" or not procutil.is_admin():
        return
    if not task_exists(TASK_DEMAND) or not task_command_matches(TASK_DEMAND):
        create_task(TASK_DEMAND, autostart=False)
    # Autostart task is created on demand by autostart.enable(); keep path in sync if present.
    if task_exists(TASK_AUTOSTART) and not task_command_matches(TASK_AUTOSTART):
        enabled = task_enabled(TASK_AUTOSTART)
        create_task(TASK_AUTOSTART, autostart=True)
        if not enabled:
            set_task_enabled(TASK_AUTOSTART, False)


def ensure_elevated_gui(flags: list[str] | None = None) -> None:
    """Relaunch elevated before QApplication. No-op if already admin or opted out."""
    if sys.platform != "win32" or _skip_elevate():
        return
    if procutil.is_admin():
        ensure_scheduled_tasks()
        try:
            from desktop import autostart

            autostart.migrate_legacy_run()
        except Exception:  # noqa: BLE001
            pass
        return

    flags = list(flags or [])
    want_autostart = "--autostart" in flags
    task = TASK_AUTOSTART if want_autostart else TASK_DEMAND
    if task_exists(task) and task_command_matches(task) and run_task(task):
        os._exit(0)

    from desktop.paths import gui_command

    cwd = str(data_root() if is_frozen() else Path(__file__).resolve().parent.parent)
    if is_frozen():
        cwd = str(Path(sys.executable).resolve().parent)
    ok = procutil.relaunch_as_admin(gui_command(*flags), cwd=cwd)
    if ok:
        os._exit(0)
