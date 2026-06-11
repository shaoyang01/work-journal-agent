from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


KNOWN_EVENT_TYPES = {
    "user_prompt",
    "tool_result",
    "conclusion",
    "note",
    "session_end",
    "assistant_stop",
}


@dataclass(frozen=True)
class WorkEvent:
    id: str
    source: str
    event_type: str
    occurred_at: datetime
    cwd: str | None
    summary: str
    raw_request: str | None = None
    decision: str | None = None
    files: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        source: str,
        event_type: str,
        summary: str,
        occurred_at: datetime | None = None,
        cwd: str | None = None,
        raw_request: str | None = None,
        decision: str | None = None,
        files: Iterable[str] = (),
        metadata: dict[str, Any] | None = None,
    ) -> "WorkEvent":
        clean_summary = " ".join(summary.split())
        if not clean_summary:
            raise ValueError("summary must not be empty")
        return cls(
            id=str(uuid.uuid4()),
            source=source.strip() or "manual",
            event_type=normalize_event_type(event_type),
            occurred_at=occurred_at or datetime.now(timezone.utc).astimezone(),
            cwd=str(Path(cwd).expanduser()) if cwd else None,
            summary=clean_summary,
            raw_request=raw_request.strip() if raw_request else None,
            decision=decision.strip() if decision else None,
            files=tuple(dict.fromkeys(file for file in files if file)),
            metadata=metadata or {},
        )

    def to_json_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
            "cwd": self.cwd,
            "summary": self.summary,
            "raw_request": self.raw_request,
            "decision": self.decision,
            "files": list(self.files),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkEvent":
        occurred_at = parse_datetime(data["occurred_at"])
        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            source=str(data.get("source") or "manual"),
            event_type=normalize_event_type(str(data.get("event_type") or "note")),
            occurred_at=occurred_at,
            cwd=data.get("cwd"),
            summary=str(data.get("summary") or "").strip(),
            raw_request=data.get("raw_request"),
            decision=data.get("decision"),
            files=tuple(data.get("files") or ()),
            metadata=dict(data.get("metadata") or {}),
        )


def normalize_event_type(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "userpromptsubmit": "user_prompt",
        "user_prompt_submit": "user_prompt",
        "posttooluse": "tool_result",
        "post_tool_use": "tool_result",
        "stop": "assistant_stop",
        "sessionend": "session_end",
        "session_end": "session_end",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in KNOWN_EVENT_TYPES else "note"


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc).astimezone()
    return parsed.astimezone()


def append_event(path: Path, event: WorkEvent) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(event.to_json_line())
        handle.write("\n")


def read_events(path: Path) -> list[WorkEvent]:
    if not path.exists():
        return []
    events: list[WorkEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = WorkEvent.from_dict(json.loads(stripped))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid event at {path}:{line_number}: {exc}") from exc
            if event.summary:
                events.append(event)
    return events


def truncate_text(value: str | None, limit: int) -> str | None:
    if not value or limit == 0:
        return None
    if limit < 0 or len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def keywords(text: str) -> set[str]:
    lowered = text.lower()
    ascii_words = re.findall(r"[a-z0-9_]+", lowered)
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", lowered)
    chinese_grams: set[str] = set()
    for run in chinese_runs:
        if len(run) <= 2:
            chinese_grams.add(run)
            continue
        chinese_grams.update(run[index : index + 2] for index in range(len(run) - 1))
    return {word for word in ascii_words if len(word) > 1} | {word for word in chinese_grams if len(word) > 1}
