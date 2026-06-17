from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import AppConfig, default_config_path, default_data_dir, default_kun_storage_root, default_opencode_storage_root, default_zcode_storage_root, expand_path, load_config
from .requirements import load_status
from .setup import create_config, create_secrets_file, configure_claude_hooks, configure_opencode_plugin, default_opencode_plugin_path


def build_app_status(config_path: Path | None = None) -> dict[str, Any]:
    path = resolved_config_path(config_path)
    config = load_config(path)
    status = load_status(storage=config.storage)
    return {
        "ok": True,
        "config_path": str(path),
        "config_exists": path.exists(),
        "data_dir": str(default_data_dir()),
        "database_path": str(config.storage.database_path or default_data_dir() / "work-journal.db"),
        "status": status,
    }


def build_config_payload(config_path: Path | None = None) -> dict[str, Any]:
    path = resolved_config_path(config_path)
    config = load_config(path)
    secret_path = path.parent / "secrets.env"
    return {
        "ok": True,
        "config_path": str(path),
        "config_exists": path.exists(),
        "storage": {
            "database_path": str(config.storage.database_path or default_data_dir() / "work-journal.db"),
            "output_dir": str(config.storage.output_dir),
        },
        "obsidian": {
            "vault_path": str(config.obsidian.vault_path) if config.obsidian.vault_path else "",
            "daily_dir": config.obsidian.daily_dir,
            "task_dir": config.obsidian.task_dir,
            "write_task_notes": config.obsidian.write_task_notes,
            "knowledge_dir": config.obsidian.knowledge_dir,
            "write_knowledge_notes": config.obsidian.write_knowledge_notes,
        },
        "ai": {
            "enabled": config.ai.enabled,
            "provider": config.ai.provider,
            "base_url": config.ai.base_url,
            "model": config.ai.model,
            "timeout_seconds": config.ai.timeout_seconds,
            "cache_enabled": config.ai.cache_enabled,
            "cache_retention_days": config.ai.cache_retention_days,
            "cluster_review_enabled": config.ai.cluster_review_enabled,
            "cluster_review_timeout_seconds": config.ai.cluster_review_timeout_seconds,
            "cluster_review_min_confidence": config.ai.cluster_review_min_confidence,
            "knowledge_enabled": config.ai.knowledge_enabled,
            "has_api_key": has_deepseek_secret(secret_path),
        },
        "sources": {
            "codex": {
                "enabled": config.sources.codex.enabled,
                "sessions_root": str(config.sources.codex.sessions_root),
            },
            "claude": {
                "enabled": config.sources.claude.enabled,
                "settings_path": str(config.sources.claude.settings_path),
            },
            "opencode": {
                "enabled": config.sources.opencode.enabled,
                "storage_root": str(config.sources.opencode.storage_root),
                "plugin_path": str(config.sources.opencode.plugin_path),
            },
            "kun": {
                "enabled": config.sources.kun.enabled,
                "storage_root": str(config.sources.kun.storage_root),
                "project_root": str(config.sources.kun.project_root),
            },
            "zcode": {
                "enabled": config.sources.zcode.enabled,
                "storage_root": str(config.sources.zcode.storage_root),
            },
        },
    }


def save_config_payload(payload: dict[str, Any], *, project_root: Path, config_path: Path | None = None) -> dict[str, Any]:
    path = expand_path(payload.get("config_path") or resolved_config_path(config_path))
    storage = object_value(payload.get("storage"))
    obsidian = object_value(payload.get("obsidian"))
    ai = object_value(payload.get("ai"))
    sources = object_value(payload.get("sources"))
    codex = object_value(sources.get("codex"))
    claude = object_value(sources.get("claude"))
    opencode = object_value(sources.get("opencode"))
    kun = object_value(sources.get("kun"))
    zcode = object_value(sources.get("zcode"))

    database_path = path_value(storage.get("database_path"), default_data_dir() / "work-journal.db")
    inbox_path = path_value(storage.get("inbox_path"), default_data_dir() / "inbox" / "events.jsonl")
    output_dir = path_value(storage.get("output_dir"), default_data_dir() / "out")
    obsidian_vault = optional_path(obsidian.get("vault_path"))
    daily_dir = str_value(obsidian.get("daily_dir"), "Daily")
    task_dir = str_value(obsidian.get("task_dir"), "Tasks")
    knowledge_dir = str_value(obsidian.get("knowledge_dir"), "Knowledge")
    codex_sessions_root = path_value(codex.get("sessions_root"), Path.home() / ".codex" / "sessions")
    claude_settings_path = path_value(claude.get("settings_path"), Path.home() / ".claude" / "settings.json")
    opencode_storage_root = path_value(opencode.get("storage_root"), default_opencode_storage_root())
    opencode_plugin_path = path_value(opencode.get("plugin_path"), default_opencode_plugin_path())
    kun_storage_root = path_value(kun.get("storage_root"), default_kun_storage_root())
    kun_project_root = path_value(kun.get("project_root"), project_root)
    zcode_storage_root = path_value(zcode.get("storage_root"), default_zcode_storage_root())

    create_config(
        config_path=path,
        inbox_path=inbox_path,
        database_path=database_path,
        output_dir=output_dir,
        obsidian_vault=obsidian_vault,
        daily_dir=daily_dir,
        task_dir=task_dir,
        write_task_notes=bool(obsidian.get("write_task_notes", False)),
        knowledge_dir=knowledge_dir,
        write_knowledge_notes=bool(obsidian.get("write_knowledge_notes", False)),
        enable_ai=bool(ai.get("enabled", False)),
        enable_codex=bool(codex.get("enabled", True)),
        codex_sessions_root=codex_sessions_root,
        enable_claude=bool(claude.get("enabled", False)),
        claude_settings_path=claude_settings_path,
        enable_opencode=bool(opencode.get("enabled", False)),
        opencode_storage_root=opencode_storage_root,
        opencode_plugin_path=opencode_plugin_path,
        enable_kun=bool(kun.get("enabled", False)),
        kun_storage_root=kun_storage_root,
        kun_project_root=kun_project_root,
        enable_zcode=bool(zcode.get("enabled", False)),
        zcode_storage_root=zcode_storage_root,
        ai_model=str_value(ai.get("model"), "deepseek-v4-pro"),
        ai_timeout_seconds=int_value(ai.get("timeout_seconds"), 180),
        ai_cache_enabled=bool(ai.get("cache_enabled", True)),
        ai_cache_retention_days=int_value(ai.get("cache_retention_days"), 7),
        ai_cluster_review_enabled=bool(ai.get("cluster_review_enabled", True)),
        ai_cluster_review_timeout_seconds=int_value(ai.get("cluster_review_timeout_seconds"), 240),
        ai_cluster_review_min_confidence=float_value(ai.get("cluster_review_min_confidence"), 0.75),
        ai_knowledge_enabled=bool(ai.get("knowledge_enabled", False)),
    )

    api_key = str(ai.get("api_key") or "").strip()
    if api_key:
        create_secrets_file(path.parent / "secrets.env", api_key)

    database_path.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    if obsidian_vault:
        (obsidian_vault / daily_dir).mkdir(parents=True, exist_ok=True)
        if bool(obsidian.get("write_task_notes", False)):
            (obsidian_vault / task_dir).mkdir(parents=True, exist_ok=True)
        if bool(obsidian.get("write_knowledge_notes", False)):
            (obsidian_vault / knowledge_dir).mkdir(parents=True, exist_ok=True)

    if bool(claude.get("enabled", False)):
        configure_claude_hooks(settings_path=claude_settings_path, project_root=project_root)
    if bool(opencode.get("enabled", False)):
        configure_opencode_plugin(plugin_path=opencode_plugin_path, config_path=path, project_root=project_root)

    return build_config_payload(path)


def resolved_config_path(config_path: Path | None = None) -> Path:
    return expand_path(config_path or os.environ.get("WJA_CONFIG") or default_config_path())


def has_deepseek_secret(path: Path) -> bool:
    if os.environ.get("DEEPSEEK_API_KEY"):
        return True
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "DEEPSEEK_API_KEY=" in text


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def object_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def str_value(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def optional_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    return expand_path(text) if text else None


def path_value(value: Any, default: Path) -> Path:
    text = str(value or "").strip()
    return expand_path(text) if text else default


def json_from_stdin(text: str) -> dict[str, Any]:
    payload = json.loads(text or "{}")
    if not isinstance(payload, dict):
        raise ValueError("JSON payload must be an object")
    return payload
