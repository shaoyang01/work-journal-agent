from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import default_config_path, default_data_dir
from .scheduler import uninstall_daily_schedule
from .setup import remove_claude_hooks


@dataclass(frozen=True)
class UninstallResult:
    schedule_removed: bool
    claude_hooks_removed: bool
    config_removed: bool
    data_removed: bool


def run_uninstall(
    *,
    project_root: Path,
    remove_config: bool = False,
    remove_data: bool = False,
    claude_settings_path: Path | None = None,
) -> UninstallResult:
    schedule_result = uninstall_daily_schedule(unload=True)
    schedule_removed = not schedule_result.path.exists()
    settings_path = claude_settings_path or Path.home() / ".claude" / "settings.json"
    claude_hooks_removed = remove_claude_hooks(settings_path=settings_path, project_root=project_root)

    config_removed = False
    config_path = default_config_path()
    if remove_config and config_path.exists():
        config_path.unlink()
        config_removed = True
    secrets_path = config_path.parent / "secrets.env"
    if remove_config and secrets_path.exists():
        secrets_path.unlink()
        config_removed = True

    data_removed = False
    data_dir = default_data_dir()
    if remove_data and data_dir.exists():
        shutil.rmtree(data_dir)
        data_removed = True

    return UninstallResult(
        schedule_removed=schedule_removed,
        claude_hooks_removed=claude_hooks_removed,
        config_removed=config_removed,
        data_removed=data_removed,
    )
