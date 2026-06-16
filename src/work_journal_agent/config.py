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
    database_path: Path | None = None


@dataclass(frozen=True)
class ObsidianConfig:
    vault_path: Path | None
    daily_dir: str
    task_dir: str
    write_task_notes: bool
    knowledge_dir: str
    write_knowledge_notes: bool


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
    cache_enabled: bool
    cache_retention_days: int
    cache_dir: Path
    cluster_review_enabled: bool
    cluster_review_timeout_seconds: int
    cluster_review_min_confidence: float
    knowledge_enabled: bool


@dataclass(frozen=True)
class CodexSourceConfig:
    enabled: bool
    sessions_root: Path


@dataclass(frozen=True)
class ClaudeSourceConfig:
    enabled: bool
    settings_path: Path


@dataclass(frozen=True)
class OpenCodeSourceConfig:
    enabled: bool
    storage_root: Path
    plugin_path: Path


@dataclass(frozen=True)
class KunSourceConfig:
    enabled: bool
    storage_root: Path
    project_root: Path


@dataclass(frozen=True)
class ZCodeSourceConfig:
    enabled: bool
    storage_root: Path


@dataclass(frozen=True)
class SourcesConfig:
    codex: CodexSourceConfig
    claude: ClaudeSourceConfig
    opencode: OpenCodeSourceConfig
    kun: KunSourceConfig
    zcode: ZCodeSourceConfig


@dataclass(frozen=True)
class AppConfig:
    storage: StorageConfig
    obsidian: ObsidianConfig
    privacy: PrivacyConfig
    merge: MergeConfig
    ai: AiConfig
    sources: SourcesConfig


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
    sources = data.get("sources", {})
    codex_source = sources.get("codex", {}) if isinstance(sources.get("codex", {}), dict) else {}
    claude_source = sources.get("claude", {}) if isinstance(sources.get("claude", {}), dict) else {}
    opencode_source = sources.get("opencode", {}) if isinstance(sources.get("opencode", {}), dict) else {}
    kun_source = sources.get("kun", {}) if isinstance(sources.get("kun", {}), dict) else {}
    zcode_source = sources.get("zcode", {}) if isinstance(sources.get("zcode", {}), dict) else {}

    data_dir = default_data_dir()
    inbox_path = expand_path(storage.get("inbox_path", data_dir / "inbox" / "events.jsonl"), base_dir=config_base)
    database_path = expand_path(storage.get("database_path", data_dir / "work-journal.db"), base_dir=config_base)
    output_dir = expand_path(storage.get("output_dir", Path.cwd() / "out"), base_dir=config_base)

    vault_value = strip_wrapping_quotes(str(obsidian.get("vault_path", "")))
    vault_path = expand_path(vault_value, base_dir=config_base) if vault_value.strip() else None

    return AppConfig(
        storage=StorageConfig(
            inbox_path=inbox_path,
            output_dir=output_dir,
            database_path=database_path,
        ),
        obsidian=ObsidianConfig(
            vault_path=vault_path,
            daily_dir=str(obsidian.get("daily_dir", "Daily")),
            task_dir=str(obsidian.get("task_dir", "Tasks")),
            write_task_notes=bool(obsidian.get("write_task_notes", False)),
            knowledge_dir=str(obsidian.get("knowledge_dir", "Knowledge")),
            write_knowledge_notes=bool(obsidian.get("write_knowledge_notes", False)),
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
            timeout_seconds=int(ai.get("timeout_seconds", 180)),
            cache_enabled=bool(ai.get("cache_enabled", True)),
            cache_retention_days=max(1, int(ai.get("cache_retention_days", 7))),
            cache_dir=expand_path(ai.get("cache_dir", data_dir / "ai-cache"), base_dir=config_base),
            cluster_review_enabled=bool(ai.get("cluster_review_enabled", True)),
            cluster_review_timeout_seconds=int(ai.get("cluster_review_timeout_seconds", ai.get("timeout_seconds", 180))),
            cluster_review_min_confidence=min(1.0, max(0.0, float(ai.get("cluster_review_min_confidence", 0.75)))),
            knowledge_enabled=bool(ai.get("knowledge_enabled", False)),
        ),
        sources=SourcesConfig(
            codex=CodexSourceConfig(
                enabled=bool(codex_source.get("enabled", True)),
                sessions_root=expand_path(codex_source.get("sessions_root", Path.home() / ".codex" / "sessions"), base_dir=config_base),
            ),
            claude=ClaudeSourceConfig(
                enabled=bool(claude_source.get("enabled", False)),
                settings_path=expand_path(claude_source.get("settings_path", Path.home() / ".claude" / "settings.json"), base_dir=config_base),
            ),
            opencode=OpenCodeSourceConfig(
                enabled=bool(opencode_source.get("enabled", True)),
                storage_root=expand_path(opencode_source.get("storage_root", default_opencode_storage_root()), base_dir=config_base),
                plugin_path=expand_path(opencode_source.get("plugin_path", default_opencode_plugin_path()), base_dir=config_base),
            ),
            kun=KunSourceConfig(
                enabled=bool(kun_source.get("enabled", default_kun_storage_root().exists())),
                storage_root=expand_path(kun_source.get("storage_root", default_kun_storage_root()), base_dir=config_base),
                project_root=expand_path(kun_source.get("project_root", Path.cwd()), base_dir=config_base),
            ),
            zcode=ZCodeSourceConfig(
                enabled=bool(zcode_source.get("enabled", default_zcode_storage_root().exists())),
                storage_root=expand_path(zcode_source.get("storage_root", default_zcode_storage_root()), base_dir=config_base),
            ),
        ),
    )


def default_opencode_storage_root() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "opencode" / "storage"


def default_opencode_plugin_path() -> Path:
    if platform.system() == "Windows":
        config_base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        config_base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_base / "opencode" / "plugins" / "work-journal-agent.js"


def default_kun_storage_root() -> Path:
    current = Path.home() / ".kun" / "data"
    if current.exists():
        return current
    legacy = Path.home() / ".deepseekgui" / "kun"
    if legacy.exists():
        return legacy
    return current


def default_zcode_storage_root() -> Path:
    return Path.home() / ".zcode" / "cli"
