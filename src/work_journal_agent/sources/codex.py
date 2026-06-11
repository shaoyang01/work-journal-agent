from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from ..config import AppConfig
from ..events import WorkEvent, append_event, parse_datetime, read_events, truncate_text


@dataclass(frozen=True)
class CodexImportResult:
    scanned_files: int
    imported_events: int
    events: tuple[WorkEvent, ...] = ()


def collect_new_codex_events(config: AppConfig, *, day: date, sessions_root: Path | None = None) -> CodexImportResult:
    root = sessions_root or Path.home() / ".codex" / "sessions"
    session_dir = root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
    if not session_dir.exists():
        return CodexImportResult(scanned_files=0, imported_events=0)

    existing_keys = {
        str(event.metadata.get("codex_event_key"))
        for event in read_events(config.storage.inbox_path)
        if event.metadata.get("codex_event_key")
    }
    collected: list[WorkEvent] = []
    files = sorted(session_dir.glob("rollout-*.jsonl"))
    for path in files:
        for event in events_from_session(path, config=config):
            event_key = str(event.metadata.get("codex_event_key"))
            if event_key in existing_keys:
                continue
            existing_keys.add(event_key)
            collected.append(event)
    return CodexImportResult(scanned_files=len(files), imported_events=len(collected), events=tuple(collected))


def import_codex_events(config: AppConfig, *, day: date, sessions_root: Path | None = None) -> CodexImportResult:
    result = collect_new_codex_events(config, day=day, sessions_root=sessions_root)
    for event in result.events:
        append_event(config.storage.inbox_path, event)
    return result


def events_from_session(path: Path, *, config: AppConfig) -> list[WorkEvent]:
    session_id: str | None = None
    cwd: str | None = None
    result: list[WorkEvent] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = item.get("payload") or {}
        if item.get("type") == "session_meta":
            session_id = payload.get("id") or session_id
            cwd = payload.get("cwd") or cwd
            continue
        if item.get("type") == "response_item" and payload.get("type") == "message":
            event = event_from_message(
                payload,
                timestamp=item.get("timestamp"),
                line_number=line_number,
                path=path,
                session_id=session_id,
                cwd=cwd,
                config=config,
            )
            if event:
                result.append(event)
        elif item.get("type") == "event_msg" and payload.get("type") == "patch_apply_end":
            event = event_from_patch(
                payload,
                timestamp=item.get("timestamp"),
                line_number=line_number,
                path=path,
                session_id=session_id,
                cwd=cwd,
            )
            if event:
                result.append(event)
    return result


def event_from_message(
    payload: dict[str, Any],
    *,
    timestamp: str | None,
    line_number: int,
    path: Path,
    session_id: str | None,
    cwd: str | None,
    config: AppConfig,
) -> WorkEvent | None:
    role = payload.get("role")
    if role not in {"user", "assistant"}:
        return None
    if payload.get("phase") == "commentary":
        return None
    text = content_text(payload.get("content") or [])
    if not should_keep_message(role, text):
        return None
    event_type = "user_prompt" if role == "user" else "conclusion"
    summary = summarize_text(text, prefix="Codex 用户需求" if role == "user" else "Codex 结论")
    raw_request = truncate_text(text, config.privacy.max_raw_request_chars) if role == "user" else None
    decision = truncate_text(text, 500) if role == "assistant" else None
    return WorkEvent.create(
        source="codex",
        event_type=event_type,
        occurred_at=parse_datetime(timestamp) if timestamp else None,
        cwd=cwd,
        summary=summary,
        raw_request=raw_request,
        decision=decision,
        metadata=codex_metadata(path, line_number, session_id, event_type),
    )


def event_from_patch(
    payload: dict[str, Any],
    *,
    timestamp: str | None,
    line_number: int,
    path: Path,
    session_id: str | None,
    cwd: str | None,
) -> WorkEvent | None:
    changes = payload.get("changes")
    if not isinstance(changes, dict) or not changes:
        return None
    files = list(changes.keys())
    return WorkEvent.create(
        source="codex",
        event_type="tool_result",
        occurred_at=parse_datetime(timestamp) if timestamp else None,
        cwd=cwd,
        summary=f"Codex 修改文件：{len(files)} 个",
        files=files,
        metadata=codex_metadata(path, line_number, session_id, "patch_apply_end"),
    )


def content_text(content: Iterable[Any]) -> str:
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            value = item.get("text")
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts).strip()


def should_keep_message(role: str, text: str) -> bool:
    if not text.strip():
        return False
    if role == "user":
        normalized = text.strip().lower()
        ignored_exact = {"y", "yes", "ok", "okay", "好", "好的", "嗯", "收到", "tes", "test"}
        if normalized in ignored_exact:
            return False
        ignored_prefixes = (
            "# AGENTS.md instructions",
            "# Files mentioned by the user:",
            "<environment_context>",
            "<image ",
            "<skill>",
            "<turn_aborted>",
        )
        return not text.lstrip().startswith(ignored_prefixes)
    if role == "assistant":
        return len(text.strip()) >= 8
    return False


def summarize_text(text: str, *, prefix: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    clean = " ".join(first_line.split())
    if len(clean) <= 80:
        return clean
    return f"{prefix}：{clean[:77].rstrip()}…"


def codex_metadata(path: Path, line_number: int, session_id: str | None, event_type: str) -> dict[str, str | int | None]:
    return {
        "session_id": session_id,
        "codex_session_path": str(path),
        "codex_line_number": line_number,
        "codex_event_key": f"{path}:{line_number}:{event_type}",
    }
