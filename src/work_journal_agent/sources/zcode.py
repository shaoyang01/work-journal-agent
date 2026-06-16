from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import AppConfig, default_zcode_storage_root
from ..events import WorkEvent, append_event, read_events, truncate_text
from .codex import should_keep_message, summarize_text
from .opencode import should_include_day


@dataclass(frozen=True)
class ZCodeImportResult:
    scanned_files: int
    imported_events: int
    events: tuple[WorkEvent, ...] = ()


def collect_new_zcode_events(
    config: AppConfig,
    *,
    day: date,
    storage_root: Path | None = None,
) -> ZCodeImportResult:
    root = storage_root or default_zcode_storage_root()
    existing_keys = {
        str(event.metadata.get("zcode_event_key"))
        for event in read_events(config.storage, day=day)
        if event.metadata.get("zcode_event_key")
    }
    events, scanned_files = events_from_zcode_storage(root, config=config, day=day)
    collected: list[WorkEvent] = []
    for event in events:
        event_key = str(event.metadata.get("zcode_event_key"))
        if event_key in existing_keys:
            continue
        existing_keys.add(event_key)
        collected.append(event)
    return ZCodeImportResult(scanned_files=scanned_files, imported_events=len(collected), events=tuple(collected))


def import_zcode_events(
    config: AppConfig,
    *,
    day: date,
    storage_root: Path | None = None,
) -> ZCodeImportResult:
    result = collect_new_zcode_events(config, day=day, storage_root=storage_root)
    for event in result.events:
        append_event(config.storage, event)
    return result


def events_from_zcode_storage(
    storage_root: Path,
    *,
    config: AppConfig,
    day: date | None = None,
) -> tuple[list[WorkEvent], int]:
    db_path = storage_root / "db" / "db.sqlite"
    if not db_path.exists():
        return [], 0
    events = events_from_zcode_db(db_path, config=config)
    filtered = [event for event in events if should_include_day(event, day)]
    filtered.sort(key=lambda event: (event.occurred_at, str(event.metadata.get("zcode_event_key") or "")))
    return filtered, 1


def events_from_zcode_db(db_path: Path, *, config: AppConfig) -> list[WorkEvent]:
    conn = connect_readonly(db_path)
    try:
        sessions = read_sessions(conn)
        text_events = events_from_messages(conn, sessions=sessions, config=config, db_path=db_path)
        tool_events = events_from_tool_usage(conn, sessions=sessions, db_path=db_path)
        file_events = events_from_session_diffs(sessions, db_path=db_path)
        return text_events + tool_events + file_events
    finally:
        conn.close()


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def read_sessions(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        """
        select id, directory, title, time_created, time_updated, summary_files, summary_diffs
        from session
        """
    ):
        result[str(row["id"])] = dict(row)
    return result


def events_from_messages(
    conn: sqlite3.Connection,
    *,
    sessions: dict[str, dict[str, Any]],
    config: AppConfig,
    db_path: Path,
) -> list[WorkEvent]:
    text_parts = parts_by_message(conn)
    result: list[WorkEvent] = []
    for row in conn.execute("select id, session_id, time_created, data from message order by time_created, id"):
        message = parse_json_object(row["data"])
        role = str(message.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        text = "\n".join(text_parts.get(str(row["id"]), ())).strip()
        if not should_keep_zcode_message(role, text):
            continue
        session = sessions.get(str(row["session_id"]), {})
        occurred_at = zcode_datetime(row["time_created"])
        event_type = "user_prompt" if role == "user" else "conclusion"
        prefix = "ZCode 用户需求" if role == "user" else "ZCode 结论"
        branch = zcode_branch(message)
        result.append(
            WorkEvent.create(
                source="zcode",
                event_type=event_type,
                occurred_at=occurred_at,
                cwd=zcode_cwd(message, session),
                summary=summarize_text(text, prefix=prefix),
                raw_request=truncate_text(text, config.privacy.max_raw_request_chars) if role == "user" else None,
                decision=truncate_text(text, 500) if role == "assistant" else None,
                metadata=zcode_metadata(
                    "message",
                    db_path=db_path,
                    event_key=f"message:{row['session_id']}:{row['id']}:{event_type}",
                    session_id=str(row["session_id"]),
                    message_id=str(row["id"]),
                    branch=branch,
                ),
            )
        )
    return result


def parts_by_message(conn: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
    result: dict[str, list[str]] = {}
    for row in conn.execute("select message_id, data from part order by time_created, id"):
        part = parse_json_object(row["data"])
        if part.get("type") != "text":
            continue
        text = str(part.get("text") or "").strip()
        if not text:
            continue
        result.setdefault(str(row["message_id"]), []).append(text)
    return {key: tuple(values) for key, values in result.items()}


def should_keep_zcode_message(role: str, text: str) -> bool:
    if not should_keep_message(role, text):
        return False
    stripped = text.lstrip()
    noise_prefixes = (
        "<system-reminder>",
        "# AGENTS.md instructions",
        "<environment_context>",
    )
    return not stripped.startswith(noise_prefixes)


def events_from_tool_usage(
    conn: sqlite3.Connection,
    *,
    sessions: dict[str, dict[str, Any]],
    db_path: Path,
) -> list[WorkEvent]:
    if not has_table(conn, "tool_usage"):
        return []
    result: list[WorkEvent] = []
    for row in conn.execute(
        """
        select id, session_id, turn_id, tool_call_id, tool_name, status, started_at, completed_at, exit_code
        from tool_usage
        order by started_at, id
        """
    ):
        session = sessions.get(str(row["session_id"]), {})
        status = str(row["status"] or "")
        tool_name = str(row["tool_name"] or "tool")
        summary = f"ZCode 执行工具：{tool_name}"
        if status and status not in {"completed", "running"}:
            summary = f"{summary}（{status}）"
        result.append(
            WorkEvent.create(
                source="zcode",
                event_type="tool_result",
                occurred_at=zcode_datetime(row["completed_at"] or row["started_at"]),
                cwd=string_or_none(session.get("directory")),
                summary=summary,
                metadata=zcode_metadata(
                    "tool_usage",
                    db_path=db_path,
                    event_key=f"tool:{row['session_id']}:{row['tool_call_id'] or row['id']}",
                    session_id=str(row["session_id"]),
                )
                | {
                    "tool_name": tool_name,
                    "tool_status": status,
                    "exit_code": row["exit_code"],
                    "turn_id": row["turn_id"],
                },
            )
        )
    return result


def events_from_session_diffs(sessions: dict[str, dict[str, Any]], *, db_path: Path) -> list[WorkEvent]:
    result: list[WorkEvent] = []
    for session_id, session in sessions.items():
        files = zcode_summary_files(session.get("summary_diffs") or session.get("summary_files"))
        if not files:
            continue
        result.append(
            WorkEvent.create(
                source="zcode",
                event_type="file_change",
                occurred_at=zcode_datetime(session.get("time_updated") or session.get("time_created")),
                cwd=string_or_none(session.get("directory")),
                summary=f"ZCode 修改文件：{len(files)} 个",
                files=files,
                metadata=zcode_metadata(
                    "session_diff",
                    db_path=db_path,
                    event_key=f"session-diff:{session_id}:{session.get('time_updated')}",
                    session_id=session_id,
                ),
            )
        )
    return result


def zcode_summary_files(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, int):
        return []
    parsed = parse_json(value) if isinstance(value, str) else value
    files: list[str] = []
    collect_files(parsed, files)
    return list(dict.fromkeys(files))


def collect_files(value: Any, result: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"file", "path", "filePath", "filepath", "relativePath"} and isinstance(item, str):
                result.append(item)
            else:
                collect_files(item, result)
    elif isinstance(value, list):
        for item in value:
            collect_files(item, result)
    elif isinstance(value, str) and ("/" in value or "." in Path(value).name):
        if len(value) <= 300 and "\n" not in value:
            result.append(value)


def zcode_cwd(message: dict[str, Any], session: dict[str, Any]) -> str | None:
    path = message.get("path")
    if isinstance(path, dict):
        cwd = string_or_none(path.get("cwd")) or string_or_none(path.get("root"))
        if cwd:
            return cwd
    snapshot = message.get("contextSnapshot")
    if isinstance(snapshot, dict):
        env = snapshot.get("envInfo")
        if isinstance(env, dict):
            cwd = string_or_none(env.get("cwd"))
            if cwd:
                return cwd
    return string_or_none(session.get("directory"))


def zcode_branch(message: dict[str, Any]) -> str | None:
    for key in ("branch", "gitBranch", "git_branch"):
        value = string_or_none(message.get(key))
        if value:
            return value
    snapshot = message.get("contextSnapshot")
    if isinstance(snapshot, dict):
        env = snapshot.get("envInfo")
        if isinstance(env, dict):
            for key in ("gitBranch", "git_branch", "branch"):
                value = string_or_none(env.get(key))
                if value:
                    return value
    return None


def zcode_datetime(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 10_000_000_000:
            raw = raw / 1000
        return datetime.fromtimestamp(raw, timezone.utc).astimezone()
    if isinstance(value, str) and value.strip():
        try:
            return zcode_datetime(float(value))
        except ValueError:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
            except ValueError:
                pass
    return datetime.now(timezone.utc).astimezone()


def parse_json_object(value: Any) -> dict[str, Any]:
    parsed = parse_json(value)
    return parsed if isinstance(parsed, dict) else {}


def parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("select 1 from sqlite_master where type = 'table' and name = ?", (name,)).fetchone()
    return row is not None


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def zcode_metadata(
    source_kind: str,
    *,
    db_path: Path,
    event_key: str,
    session_id: str | None = None,
    message_id: str | None = None,
    branch: str | None = None,
) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "session_id": session_id,
            "message_id": message_id,
            "branch": branch,
            "zcode_source_kind": source_kind,
            "zcode_db_path": str(db_path),
            "zcode_event_key": event_key,
        }.items()
        if value is not None
    }
