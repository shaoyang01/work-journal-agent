from __future__ import annotations

import os
import platform
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StorageConfig:
    inbox_path: Path
    output_dir: Path


@dataclass(frozen=True)
class ObsidianConfig:
    vault_path: Path | None
    daily_dir: str
    task_dir: str
    write_task_notes: bool


@dataclass(frozen=True)
class PrivacyConfig:
    max_raw_request_chars: int
    store_transcript_paths: bool


@dataclass(frozen=True)
class MergeConfig:
    min_keyword_overlap: int


@dataclass(frozen=True)
class AiConfig:
    enabled: bool
    provider: str
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: int


@dataclass(frozen=True)
class AppConfig:
    storage: StorageConfig
    obsidian: ObsidianConfig
    privacy: PrivacyConfig
    merge: MergeConfig
    ai: AiConfig


def default_config_path() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "work-journal-agent" / "config.toml"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "work-journal-agent" / "config.toml"


def default_data_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "work-journal-agent"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "work-journal-agent"


def expand_path(value: str | Path, *, base_dir: Path | None = None) -> Path:
    raw_value = strip_wrapping_quotes(str(value))
    raw = Path(os.path.expandvars(os.path.expanduser(raw_value)))
    if raw.is_absolute():
        return raw
    if base_dir is not None:
        return base_dir / raw
    return raw


def strip_wrapping_quotes(value: str) -> str:
    stripped = value.strip()
    quote_pairs = (("'", "'"), ('"', '"'))
    changed = True
    while changed and len(stripped) >= 2:
        changed = False
        for left, right in quote_pairs:
            if stripped.startswith(left) and stripped.endswith(right):
                stripped = stripped[1:-1].strip()
                changed = True
    return stripped


def load_config(config_path: Path | None = None) -> AppConfig:
    explicit_path = config_path
    if explicit_path is None and "WJA_CONFIG" in os.environ:
        explicit_path = Path(os.environ["WJA_CONFIG"])
    path = explicit_path or default_config_path()
    data: dict[str, Any] = {}
    config_base = Path.cwd()
    if path.exists():
        config_base = path.parent
        with path.open("rb") as handle:
            data = tomllib.load(handle)

    storage = data.get("storage", {})
    obsidian = data.get("obsidian", {})
    privacy = data.get("privacy", {})
    merge = data.get("merge", {})
    ai = data.get("ai", {})

    data_dir = default_data_dir()
    inbox_path = expand_path(storage.get("inbox_path", data_dir / "inbox" / "events.jsonl"), base_dir=config_base)
    output_dir = expand_path(storage.get("output_dir", Path.cwd() / "out"), base_dir=config_base)

    vault_value = strip_wrapping_quotes(str(obsidian.get("vault_path", "")))
    vault_path = expand_path(vault_value, base_dir=config_base) if vault_value.strip() else None

    return AppConfig(
        storage=StorageConfig(
            inbox_path=inbox_path,
            output_dir=output_dir,
        ),
        obsidian=ObsidianConfig(
            vault_path=vault_path,
            daily_dir=str(obsidian.get("daily_dir", "Daily")),
            task_dir=str(obsidian.get("task_dir", "Tasks")),
            write_task_notes=bool(obsidian.get("write_task_notes", False)),
        ),
        privacy=PrivacyConfig(
            max_raw_request_chars=int(privacy.get("max_raw_request_chars", 500)),
            store_transcript_paths=bool(privacy.get("store_transcript_paths", True)),
        ),
        merge=MergeConfig(
            min_keyword_overlap=max(0, int(merge.get("min_keyword_overlap", 1))),
        ),
        ai=AiConfig(
            enabled=bool(ai.get("enabled", False)),
            provider=str(ai.get("provider", "deepseek")),
            base_url=str(ai.get("base_url", "https://api.deepseek.com")),
            model=str(ai.get("model", "deepseek-v4-flash")),
            api_key_env=str(ai.get("api_key_env", "DEEPSEEK_API_KEY")),
            timeout_seconds=int(ai.get("timeout_seconds", 30)),
        ),
    )
