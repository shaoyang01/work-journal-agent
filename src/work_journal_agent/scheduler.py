from __future__ import annotations

import os
import platform
import plistlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import default_data_dir


LAUNCHD_LABEL = "com.shaoyang01.work-journal-agent.daily"


@dataclass(frozen=True)
class ScheduleResult:
    path: Path
    loaded: bool
    message: str


def install_daily_schedule(*, project_root: Path, time_text: str, load: bool = True) -> ScheduleResult:
    if platform.system() != "Darwin":
        raise ValueError("automatic scheduling is currently implemented for macOS launchd only")
    hour, minute = parse_time(time_text)
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    logs_dir = default_data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents / f"{LAUNCHD_LABEL}.plist"
    command = daily_command(project_root)
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": ["/bin/zsh", "-lc", command],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(logs_dir / "daily.out.log"),
        "StandardErrorPath": str(logs_dir / "daily.err.log"),
        "RunAtLoad": False,
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle, sort_keys=False)

    loaded = False
    message = f"created {plist_path}"
    if load:
        loaded = load_launchd_plist(plist_path)
        message = f"created and loaded {plist_path}" if loaded else f"created {plist_path}, but launchctl load failed"
    return ScheduleResult(path=plist_path, loaded=loaded, message=message)


def install_interval_schedule(*, project_root: Path, every_minutes: int = 15, load: bool = True) -> ScheduleResult:
    if platform.system() != "Darwin":
        raise ValueError("automatic scheduling is currently implemented for macOS launchd only")
    if every_minutes < 1:
        raise ValueError("every_minutes must be greater than 0")
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    logs_dir = default_data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents / f"{LAUNCHD_LABEL}.plist"
    command = daily_command(project_root)
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": ["/bin/zsh", "-lc", command],
        "StartInterval": every_minutes * 60,
        "StandardOutPath": str(logs_dir / "daily.out.log"),
        "StandardErrorPath": str(logs_dir / "daily.err.log"),
        "RunAtLoad": True,
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle, sort_keys=False)

    loaded = False
    message = f"created {plist_path}"
    if load:
        loaded = load_launchd_plist(plist_path)
        message = f"created and loaded {plist_path}" if loaded else f"created {plist_path}, but launchctl load failed"
    return ScheduleResult(path=plist_path, loaded=loaded, message=message)


def uninstall_daily_schedule(*, unload: bool = True) -> ScheduleResult:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    loaded = False
    if unload and plist_path.exists() and platform.system() == "Darwin":
        unload_launchd_plist(plist_path)
    if plist_path.exists():
        plist_path.unlink()
    return ScheduleResult(path=plist_path, loaded=loaded, message=f"removed {plist_path}")


def schedule_status() -> str:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    if not plist_path.exists():
        return f"not installed: {plist_path}"
    return f"installed: {plist_path}"


def daily_command(project_root: Path) -> str:
    quoted_root = shell_quote(project_root)
    secrets = Path.home() / ".config" / "work-journal-agent" / "secrets.env"
    quoted_secrets = shell_quote(secrets)
    return (
        f"if [ -f {quoted_secrets} ]; then source {quoted_secrets}; fi; "
        f"cd {quoted_root} && PYTHONPATH=src python3 -m work_journal_agent sync"
    )


def parse_time(value: str) -> tuple[int, int]:
    if ":" not in value:
        raise ValueError("time must use HH:MM format")
    hour_text, minute_text = value.split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("time must be between 00:00 and 23:59")
    return hour, minute


def load_launchd_plist(plist_path: Path) -> bool:
    if os.environ.get("WJA_SKIP_LAUNCHCTL") == "1":
        return False
    unload_launchd_plist(plist_path)
    user_id = os.getuid()
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{user_id}", str(plist_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        return True
    fallback = subprocess.run(
        ["launchctl", "load", str(plist_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return fallback.returncode == 0


def unload_launchd_plist(plist_path: Path) -> None:
    if os.environ.get("WJA_SKIP_LAUNCHCTL") == "1":
        return
    user_id = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{user_id}", str(plist_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def shell_quote(value: Path) -> str:
    text = str(value)
    return "'" + text.replace("'", "'\\''") + "'"
