from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import AppConfig
from ..events import WorkEvent, append_event, parse_datetime, read_events, truncate_text
from .codex import should_keep_message, summarize_text


@dataclass(frozen=True)
class OpenCodeImportResult:
    scanned_files: int
    imported_events: int
    events: tuple[WorkEvent, ...] = ()


def default_storage_root() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "opencode" / "storage"


def collect_new_opencode_events(
    config: AppConfig,
    *,
    day: date,
    storage_root: Path | None = None,
) -> OpenCodeImportResult:
    root = storage_root or default_storage_root()
    if not root.exists():
        return OpenCodeImportResult(scanned_files=0, imported_events=0)

    existing_keys = {
        str(event.metadata.get("opencode_event_key"))
        for event in read_events(config.storage.inbox_path)
        if event.metadata.get("opencode_event_key")
    }
    events, scanned_files = events_from_storage(root, config=config, day=day)
    collected: list[WorkEvent] = []
    for event in events:
        event_key = str(event.metadata.get("opencode_event_key"))
        if event_key in existing_keys:
            continue
        existing_keys.add(event_key)
        collected.append(event)
    return OpenCodeImportResult(scanned_files=scanned_files, imported_events=len(collected), events=tuple(collected))


def import_opencode_events(
    config: AppConfig,
    *,
    day: date,
    storage_root: Path | None = None,
) -> OpenCodeImportResult:
    result = collect_new_opencode_events(config, day=day, storage_root=storage_root)
    for event in result.events:
        append_event(config.storage.inbox_path, event)
    return result


def events_from_storage(root: Path, *, config: AppConfig, day: date | None = None) -> tuple[list[WorkEvent], int]:
    part_files = sorted((root / "part").glob("msg_*/*.json"))
    parts_by_message = load_parts_by_message(part_files)
    scanned_files = len(part_files)
    result: list[WorkEvent] = []

    message_files = sorted((root / "message").glob("ses_*/*.json"))
    scanned_files += len(message_files)
    for path in message_files:
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        parts = parts_by_message.get(str(payload.get("id") or ""), ())
        for event in events_from_message(payload, parts=parts, path=path, config=config):
            if should_include_day(event, day):
                result.append(event)

    diff_files = sorted((root / "session_diff").glob("ses_*.json"))
    scanned_files += len(diff_files)
    for path in diff_files:
        payload = read_json(path)
        event = event_from_session_diff(payload, path=path)
        if event and should_include_day(event, day):
            result.append(event)

    result.sort(key=lambda event: (event.occurred_at, str(event.metadata.get("opencode_event_key") or "")))
    return result, scanned_files


def load_parts_by_message(part_files: Iterable[Path]) -> dict[str, tuple[dict[str, Any], ...]]:
    parts: dict[str, list[dict[str, Any]]] = {}
    for path in part_files:
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        message_id = str(payload.get("messageID") or path.parent.name)
        payload["_opencode_part_path"] = str(path)
        parts.setdefault(message_id, []).append(payload)
    return {message_id: tuple(items) for message_id, items in parts.items()}


def events_from_message(
    message: dict[str, Any],
    *,
    parts: Iterable[dict[str, Any]],
    path: Path,
    config: AppConfig,
) -> list[WorkEvent]:
    role = str(message.get("role") or "")
    message_id = str(message.get("id") or path.stem)
    session_id = str(message.get("sessionID") or path.parent.name)
    cwd = string_or_none(message.get("path"))
    occurred_at = opencode_datetime(message.get("time")) or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).astimezone()
    result: list[WorkEvent] = []

    text = message_text(message, parts)
    if role in {"user", "assistant"} and should_keep_message(role, text):
        event_type = "user_prompt" if role == "user" else "conclusion"
        prefix = "OpenCode 用户需求" if role == "user" else "OpenCode 结论"
        result.append(
            WorkEvent.create(
                source="opencode",
                event_type=event_type,
                occurred_at=occurred_at,
                cwd=cwd,
                summary=summarize_text(text, prefix=prefix),
                raw_request=truncate_text(text, config.privacy.max_raw_request_chars) if role == "user" else None,
                decision=truncate_text(text, 500) if role == "assistant" else None,
                files=files_from_parts(parts),
                metadata=opencode_metadata(
                    "message",
                    session_id=session_id,
                    message_id=message_id,
                    path=path,
                    event_key=f"message:{session_id}:{message_id}:{event_type}",
                ),
            )
        )

    for part in parts:
        event = event_from_part(part, message=message, message_path=path, fallback_time=occurred_at, cwd=cwd)
        if event:
            result.append(event)
    return result


def event_from_part(
    part: dict[str, Any],
    *,
    message: dict[str, Any],
    message_path: Path,
    fallback_time: datetime,
    cwd: str | None,
) -> WorkEvent | None:
    part_type = str(part.get("type") or "")
    if part_type == "text":
        return None
    session_id = str(part.get("sessionID") or message.get("sessionID") or message_path.parent.name)
    message_id = str(part.get("messageID") or message.get("id") or message_path.stem)
    part_id = str(part.get("id") or Path(str(part.get("_opencode_part_path") or "")).stem)
    files = extract_files(part)
    occurred_at = opencode_datetime(part.get("time")) or fallback_time
    metadata = opencode_metadata(
        "part",
        session_id=session_id,
        message_id=message_id,
        part_id=part_id,
        path=Path(str(part.get("_opencode_part_path") or message_path)),
        event_key=f"part:{session_id}:{message_id}:{part_id}:{part_type}",
    )
    metadata["part_type"] = part_type

    if part_type == "tool":
        tool_name = string_or_none(part.get("tool")) or "unknown"
        status = nested_get(part, ("state", "status"))
        if status:
            metadata["status"] = status
        metadata["tool_name"] = tool_name
        return WorkEvent.create(
            source="opencode",
            event_type="tool_result",
            occurred_at=occurred_at,
            cwd=cwd,
            summary=f"OpenCode 执行工具：{tool_name}",
            files=files,
            metadata=metadata,
        )
    if part_type == "patch":
        files = files or files_from_patch_part(part)
        if not files:
            return None
        return WorkEvent.create(
            source="opencode",
            event_type="file_change",
            occurred_at=occurred_at,
            cwd=cwd,
            summary=f"OpenCode 生成补丁：{len(files)} 个文件",
            files=files,
            metadata=metadata,
        )
    if part_type == "file":
        if not files:
            return None
        return WorkEvent.create(
            source="opencode",
            event_type="note",
            occurred_at=occurred_at,
            cwd=cwd,
            summary=f"OpenCode 引用文件：{', '.join(files[:3])}",
            files=files,
            metadata=metadata,
        )
    return None


def event_from_session_diff(payload: Any, *, path: Path) -> WorkEvent | None:
    if not isinstance(payload, list):
        return None
    files: list[str] = []
    additions = 0
    deletions = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        file_path = string_or_none(item.get("file"))
        if file_path:
            files.append(file_path)
        additions += int(item.get("additions") or 0)
        deletions += int(item.get("deletions") or 0)
    files = list(dict.fromkeys(files))
    if not files:
        return None
    session_id = path.stem
    return WorkEvent.create(
        source="opencode",
        event_type="file_change",
        occurred_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).astimezone(),
        summary=f"OpenCode 修改文件：{len(files)} 个（+{additions}/-{deletions}）",
        files=files,
        metadata=opencode_metadata(
            "session_diff",
            session_id=session_id,
            path=path,
            event_key=f"session_diff:{session_id}:{path.stat().st_mtime_ns}",
        )
        | {"additions": additions, "deletions": deletions},
    )


def event_from_hook_payload(payload: dict[str, Any], *, config: AppConfig, event_type_override: str | None = None) -> WorkEvent:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
    event_name = str(event.get("type") or event.get("event") or event_type_override or "note")
    cwd = first_string(payload, "cwd", "directory", "worktree") or first_string(event, "cwd", "directory", "worktree")
    session_id = first_string(event, "sessionID", "sessionId", "session_id") or first_string(payload, "sessionID", "sessionId", "session_id")
    files = extract_files(event) or extract_files(payload)
    text = text_from_hook_event(event)
    occurred_at = opencode_datetime(event.get("time")) or parse_hook_timestamp(event.get("timestamp") or payload.get("timestamp"))
    event_key_time = occurred_at.isoformat() if occurred_at else datetime.now(timezone.utc).astimezone().isoformat()
    metadata = {
        "opencode_event_type": event_name,
        "session_id": session_id,
        "opencode_event_key": first_string(event, "id") or f"hook:{event_name}:{session_id or ''}:{event_key_time}",
    }
    metadata = {key: value for key, value in metadata.items() if value}

    if event_type_override:
        event_type = event_type_override
    elif event_name.startswith("message") and text:
        role = str(event.get("role") or nested_get(event, ("message", "role")) or "")
        event_type = "user_prompt" if role == "user" else "conclusion"
    elif event_name in {"file.edited", "file.watcher.updated", "session.diff"}:
        event_type = "file_change"
    elif event_name.startswith("tool.execute"):
        event_type = "tool_result"
    elif event_name == "session.idle":
        event_type = "assistant_stop"
    else:
        event_type = "note"

    if text:
        prefix = "OpenCode 用户需求" if event_type == "user_prompt" else "OpenCode 事件"
        summary = summarize_text(text, prefix=prefix)
    elif event_type == "file_change":
        summary = f"OpenCode 文件变更：{len(files)} 个文件" if files else f"OpenCode 事件：{event_name}"
    elif event_type == "tool_result":
        tool_name = first_string(event, "tool") or str(nested_get(event, ("input", "tool")) or "unknown")
        metadata["tool_name"] = tool_name
        summary = f"OpenCode 执行工具：{tool_name}"
    else:
        summary = f"OpenCode 事件：{event_name}"

    return WorkEvent.create(
        source="opencode",
        event_type=event_type,
        occurred_at=occurred_at,
        cwd=cwd,
        summary=summary,
        raw_request=truncate_text(text, config.privacy.max_raw_request_chars) if event_type == "user_prompt" else None,
        decision=truncate_text(text, 500) if event_type == "conclusion" else None,
        files=files,
        metadata=metadata,
    )


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def message_text(message: dict[str, Any], parts: Iterable[dict[str, Any]]) -> str:
    text_parts = [str(part.get("text")) for part in parts if part.get("type") == "text" and isinstance(part.get("text"), str)]
    if text_parts:
        return "\n".join(text_parts).strip()
    summary = message.get("summary")
    return str(summary).strip() if isinstance(summary, str) else ""


def files_from_parts(parts: Iterable[dict[str, Any]]) -> list[str]:
    files: list[str] = []
    for part in parts:
        files.extend(extract_files(part))
        if part.get("type") == "patch":
            files.extend(files_from_patch_part(part))
    return list(dict.fromkeys(files))


def extract_files(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    files: list[str] = []
    for key in ("file", "path", "filePath", "file_path", "filename", "url"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            files.append(value)
    state = payload.get("state")
    if isinstance(state, dict):
        files.extend(extract_files(state))
        input_value = state.get("input")
        if isinstance(input_value, dict):
            files.extend(extract_files(input_value))
    for key in ("input", "output", "properties", "message"):
        value = payload.get(key)
        if isinstance(value, dict):
            files.extend(extract_files(value))
    file_values = payload.get("files")
    if isinstance(file_values, list):
        for item in file_values:
            if isinstance(item, str):
                files.append(item)
            elif isinstance(item, dict):
                files.extend(extract_files(item))
    return list(dict.fromkeys(files))


def files_from_patch_part(part: dict[str, Any]) -> list[str]:
    files = part.get("files")
    if not isinstance(files, list):
        return []
    result: list[str] = []
    for item in files:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            value = string_or_none(item.get("path")) or string_or_none(item.get("file")) or string_or_none(item.get("filename"))
            if value:
                result.append(value)
    return list(dict.fromkeys(result))


def opencode_datetime(value: Any) -> datetime | None:
    if isinstance(value, dict):
        for key in ("completed", "end", "created", "start"):
            parsed = opencode_datetime(value.get(key))
            if parsed:
                return parsed
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, timezone.utc).astimezone()
    if isinstance(value, str) and value.strip():
        try:
            return parse_datetime(value)
        except ValueError:
            return None
    return None


def parse_hook_timestamp(value: Any) -> datetime | None:
    if isinstance(value, str) and value.strip():
        try:
            return parse_datetime(value)
        except ValueError:
            return None
    return None


def should_include_day(event: WorkEvent, day: date | None) -> bool:
    return day is None or event.occurred_at.date() == day


def text_from_hook_event(event: dict[str, Any]) -> str:
    text = event.get("text")
    if isinstance(text, str):
        return text.strip()
    message = event.get("message")
    if isinstance(message, dict):
        nested_text = message.get("text") or message.get("summary")
        if isinstance(nested_text, str):
            return nested_text.strip()
    parts = event.get("parts")
    if isinstance(parts, list):
        return "\n".join(str(part.get("text")) for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)).strip()
    return ""


def first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def nested_get(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def opencode_metadata(
    source_kind: str,
    *,
    session_id: str | None = None,
    message_id: str | None = None,
    part_id: str | None = None,
    path: Path,
    event_key: str,
) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "session_id": session_id,
            "message_id": message_id,
            "part_id": part_id,
            "opencode_source_kind": source_kind,
            "opencode_path": str(path),
            "opencode_event_key": event_key,
        }.items()
        if value
    }
