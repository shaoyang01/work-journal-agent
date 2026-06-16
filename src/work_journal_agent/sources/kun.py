from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import AppConfig, default_kun_storage_root
from ..events import WorkEvent, append_event, parse_datetime, read_events, truncate_text
from .codex import should_keep_message, summarize_text
from .opencode import extract_files, opencode_datetime, should_include_day


@dataclass(frozen=True)
class KunImportResult:
    scanned_files: int
    imported_events: int
    events: tuple[WorkEvent, ...] = ()


def collect_new_kun_events(
    config: AppConfig,
    *,
    day: date,
    storage_root: Path | None = None,
    project_root: Path | None = None,
) -> KunImportResult:
    root = storage_root or default_kun_storage_root()
    project = project_root or config.sources.kun.project_root
    existing_keys = {
        str(event.metadata.get("kun_event_key"))
        for event in read_events(config.storage, day=day)
        if event.metadata.get("kun_event_key")
    }
    events, scanned_files = events_from_kun_sources(root, project_root=project, config=config, day=day)
    collected: list[WorkEvent] = []
    for event in events:
        event_key = str(event.metadata.get("kun_event_key"))
        if event_key in existing_keys:
            continue
        existing_keys.add(event_key)
        collected.append(event)
    return KunImportResult(scanned_files=scanned_files, imported_events=len(collected), events=tuple(collected))


def import_kun_events(
    config: AppConfig,
    *,
    day: date,
    storage_root: Path | None = None,
    project_root: Path | None = None,
) -> KunImportResult:
    result = collect_new_kun_events(config, day=day, storage_root=storage_root, project_root=project_root)
    for event in result.events:
        append_event(config.storage, event)
    return result


def events_from_kun_sources(
    storage_root: Path,
    *,
    project_root: Path,
    config: AppConfig,
    day: date | None = None,
) -> tuple[list[WorkEvent], int]:
    result: list[WorkEvent] = []
    scanned_files = 0

    if storage_root.exists():
        for thread_dir in sorted((storage_root / "threads").glob("*")):
            if not thread_dir.is_dir():
                continue
            thread = read_thread_metadata(thread_dir)
            for name, parser in (("messages.jsonl", events_from_messages_file), ("events.jsonl", events_from_events_file)):
                path = thread_dir / name
                if not path.exists():
                    continue
                scanned_files += 1
                for event in parser(path, thread=thread, config=config):
                    if should_include_day(event, day):
                        result.append(event)

    for doc_path in kun_sdd_files(project_root):
        scanned_files += 1
        event = event_from_kunsdd_file(doc_path, project_root=project_root)
        if event and should_include_day(event, day):
            result.append(event)

    result.sort(key=lambda event: (event.occurred_at, str(event.metadata.get("kun_event_key") or "")))
    return result, scanned_files


def events_from_messages_file(path: Path, *, thread: dict[str, Any], config: AppConfig) -> list[WorkEvent]:
    result: list[WorkEvent] = []
    for line_number, item in enumerate(read_jsonl(path), start=1):
        message = object_value(item.get("message")) or item
        role = str(message.get("role") or message.get("sender") or "")
        if role not in {"user", "assistant"}:
            continue
        kind = str(message.get("kind") or "")
        text = message_text(message)
        if not should_keep_kun_message(role, kind=kind, text=text):
            continue
        event_type = "user_prompt" if role == "user" else "conclusion"
        prefix = "Kun 用户需求" if role == "user" else "Kun 结论"
        occurred_at = kun_datetime(first_present(message, "createdAt", "created_at", "timestamp", "time")) or file_time(path)
        thread_id = str(first_present(message, "threadId", "thread_id") or thread.get("id") or path.parent.name)
        message_id = str(first_present(message, "id", "messageId", "message_id") or line_number)
        cwd = string_or_none(first_present(message, "cwd", "projectRoot", "workspace")) or string_or_none(thread.get("cwd")) or string_or_none(thread.get("projectRoot")) or string_or_none(thread.get("workspace"))
        files = extract_files(message)
        result.append(
            WorkEvent.create(
                source="kun",
                event_type=event_type,
                occurred_at=occurred_at,
                cwd=cwd,
                summary=summarize_text(text, prefix=prefix),
                raw_request=truncate_text(text, config.privacy.max_raw_request_chars) if role == "user" else None,
                decision=truncate_text(text, 500) if role == "assistant" else None,
                files=files,
                metadata=kun_metadata(
                    "message",
                    path=path,
                    event_key=f"message:{thread_id}:{message_id}:{event_type}",
                    thread_id=thread_id,
                    message_id=message_id,
                    line_number=line_number,
                ),
            )
        )
    return result


def events_from_events_file(path: Path, *, thread: dict[str, Any], config: AppConfig) -> list[WorkEvent]:
    result: list[WorkEvent] = []
    for line_number, item in enumerate(read_jsonl(path), start=1):
        event = object_value(item.get("event")) or item
        event_name = str(first_present(event, "type", "event", "phase", "name") or "note")
        text = message_text(event)
        files = extract_files(event)
        occurred_at = kun_datetime(first_present(event, "createdAt", "created_at", "timestamp", "time")) or file_time(path)
        thread_id = str(first_present(event, "threadId", "thread_id") or thread.get("id") or path.parent.name)
        event_id = str(first_present(event, "id", "eventId", "event_id") or line_number)
        cwd = string_or_none(first_present(event, "cwd", "projectRoot", "workspace")) or string_or_none(thread.get("cwd")) or string_or_none(thread.get("projectRoot")) or string_or_none(thread.get("workspace"))
        event_type = normalized_kun_event_type(event_name, text=text, files=files)
        if event_type == "note" and not text and not files:
            continue
        summary = summary_for_kun_event(event_name, event_type=event_type, text=text, files=files)
        result.append(
            WorkEvent.create(
                source="kun",
                event_type=event_type,
                occurred_at=occurred_at,
                cwd=cwd,
                summary=summary,
                raw_request=truncate_text(text, config.privacy.max_raw_request_chars) if event_type == "user_prompt" else None,
                decision=truncate_text(text, 500) if event_type == "conclusion" else None,
                files=files,
                metadata=kun_metadata(
                    "event",
                    path=path,
                    event_key=f"event:{thread_id}:{event_id}:{event_name}",
                    thread_id=thread_id,
                    line_number=line_number,
                )
                | {"kun_event_name": event_name},
            )
        )
    return result


def event_from_kunsdd_file(path: Path, *, project_root: Path) -> WorkEvent | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    title = first_markdown_heading(text) or path.stem
    lowered = str(path).lower()
    if "requirement" in lowered or "需求" in str(path):
        event_type = "user_prompt"
        summary = f"Kun 需求文档：{title}"
        raw_request = truncate_text(text, 1000)
    elif "plan" in lowered or "方案" in str(path):
        event_type = "note"
        summary = f"Kun 方案文档：{title}"
        raw_request = None
    else:
        event_type = "note"
        summary = f"Kun SDD 文档：{title}"
        raw_request = None
    return WorkEvent.create(
        source="kun",
        event_type=event_type,
        occurred_at=file_time(path),
        cwd=str(project_root),
        summary=summary,
        raw_request=raw_request,
        files=[str(path)],
        metadata=kun_metadata(
            "kunsdd",
            path=path,
            event_key=f"kunsdd:{path}:{path.stat().st_mtime_ns}",
        ),
    )


def kun_sdd_files(project_root: Path) -> list[Path]:
    root = project_root / ".kunsdd"
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in {".md", ".markdown", ".txt"})


def normalized_kun_event_type(event_name: str, *, text: str, files: list[str]) -> str:
    lowered = event_name.lower()
    if "userpromptsubmit" in lowered or "user_prompt" in lowered or lowered == "user":
        return "user_prompt"
    if "turnend" in lowered or "assistant" in lowered or "summary" in lowered:
        return "conclusion" if text else "assistant_stop"
    if files or "file" in lowered or "write" in lowered or "edit" in lowered:
        return "file_change"
    if "posttooluse" in lowered or "tool" in lowered:
        return "tool_result"
    return "note"


def should_keep_kun_message(role: str, *, kind: str, text: str) -> bool:
    if not should_keep_message(role, text):
        return False
    if role == "user":
        return kind in {"", "user_message", "message"}
    if role == "assistant":
        return kind in {"", "assistant_message", "assistant_text", "message"}
    return False


def summary_for_kun_event(event_name: str, *, event_type: str, text: str, files: list[str]) -> str:
    if text:
        prefix = "Kun 用户需求" if event_type == "user_prompt" else "Kun 事件"
        return summarize_text(text, prefix=prefix)
    if event_type == "file_change":
        return f"Kun 文件变更：{len(files)} 个文件" if files else f"Kun 事件：{event_name}"
    if event_type == "tool_result":
        return f"Kun 执行工具：{event_name}"
    return f"Kun 事件：{event_name}"


def message_text(payload: dict[str, Any]) -> str:
    for key in ("text", "content", "summary", "prompt", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    parts = payload.get("parts") or payload.get("content")
    if isinstance(parts, list):
        texts: list[str] = []
        for part in parts:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict):
                value = part.get("text") or part.get("content")
                if isinstance(value, str):
                    texts.append(value)
        return "\n".join(texts).strip()
    return ""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return result
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_thread_metadata(thread_dir: Path) -> dict[str, Any]:
    thread = read_json_object(thread_dir / "thread.json")
    for item in read_jsonl(thread_dir / "metadata.jsonl"):
        nested = object_value(item.get("thread"))
        if nested:
            thread.update(nested)
    if "id" not in thread:
        thread["id"] = thread_dir.name
    return thread


def kun_datetime(value: Any) -> datetime | None:
    parsed = opencode_datetime(value)
    if parsed:
        return parsed
    if isinstance(value, str) and value.strip():
        try:
            return parse_datetime(value)
        except ValueError:
            return None
    return None


def file_time(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).astimezone()


def first_markdown_heading(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    return None


def first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def object_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def kun_metadata(source_kind: str, *, path: Path, event_key: str, thread_id: str | None = None, message_id: str | None = None, line_number: int | None = None) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "session_id": thread_id,
            "thread_id": thread_id,
            "message_id": message_id,
            "kun_source_kind": source_kind,
            "kun_path": str(path),
            "kun_line_number": line_number,
            "kun_event_key": event_key,
        }.items()
        if value is not None
    }
