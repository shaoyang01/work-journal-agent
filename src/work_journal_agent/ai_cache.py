from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .merge import TaskSummary, repo_identity


CACHE_VERSION = 1


@dataclass(frozen=True)
class CacheMatch:
    entries: tuple[dict[str, Any], ...]

    @property
    def event_ids(self) -> set[str]:
        result: set[str] = set()
        for entry in self.entries:
            result.update(str(value) for value in entry.get("event_ids") or [])
        return result

    @property
    def ai_results(self) -> list[dict[str, Any]]:
        return [dict(entry.get("ai_result") or {}) for entry in self.entries if isinstance(entry.get("ai_result"), dict)]

    @property
    def contexts(self) -> list[dict[str, Any]]:
        return [dict(entry.get("context") or {}) for entry in self.entries if isinstance(entry.get("context"), dict)]


def cache_path(cache_dir: Path, day: date) -> Path:
    return cache_dir / f"{day.isoformat()}.json"


def load_cache(cache_dir: Path, day: date) -> dict[str, Any]:
    path = cache_path(cache_dir, day)
    if not path.exists():
        return {"cache_version": CACHE_VERSION, "date": day.isoformat(), "tasks": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"cache_version": CACHE_VERSION, "date": day.isoformat(), "tasks": []}
    if not isinstance(value, dict):
        return {"cache_version": CACHE_VERSION, "date": day.isoformat(), "tasks": []}
    tasks = value.get("tasks")
    if not isinstance(tasks, list):
        value["tasks"] = []
    return value


def save_cache(cache_dir: Path, day: date, entries: list[dict[str, Any]]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "cache_version": CACHE_VERSION,
        "date": day.isoformat(),
        "updated_at": datetime.now().astimezone().isoformat(),
        "tasks": entries,
    }
    cache_path(cache_dir, day).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prune_cache(cache_dir: Path, *, keep_days: int, today: date) -> None:
    if not cache_dir.exists():
        return
    cutoff = today - timedelta(days=max(1, keep_days) - 1)
    for path in cache_dir.glob("*.json"):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day < cutoff:
            path.unlink()


def find_cache_match(task: TaskSummary, entries: list[dict[str, Any]]) -> CacheMatch | None:
    repo = repo_identity(task.cwd)
    task_event_ids = set(task.event_ids)
    strong_matches = [
        entry
        for entry in entries
        if repo == str(entry.get("repo") or "") and task_event_ids.intersection(set(str(value) for value in entry.get("event_ids") or []))
    ]
    if strong_matches:
        return CacheMatch(tuple(strong_matches))

    session_matches = [
        entry
        for entry in entries
        if repo == str(entry.get("repo") or "") and task.session_ids.intersection(set(str(value) for value in entry.get("session_ids") or []))
    ]
    if session_matches:
        return CacheMatch(tuple(session_matches))

    file_matches = [
        entry
        for entry in entries
        if repo == str(entry.get("repo") or "") and task.files.intersection(set(str(value) for value in entry.get("files") or []))
    ]
    if file_matches:
        return CacheMatch(tuple(file_matches))

    task_words = set(task.title.lower().split())
    if task_words:
        title_matches = [
            entry
            for entry in entries
            if repo == str(entry.get("repo") or "") and task_words.intersection(str(entry.get("title") or "").lower().split())
        ]
        if title_matches:
            return CacheMatch(tuple(title_matches[:1]))
    return None


def task_cache_entry(task: TaskSummary, *, context: dict[str, Any], ai_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "cache_task_id": f"{task.day.isoformat()}:{repo_identity(task.cwd)}:{task.key}",
        "key": task.key,
        "repo": repo_identity(task.cwd),
        "title": task.ai_title or task.title,
        "event_ids": sorted(task.event_ids),
        "session_ids": sorted(task.session_ids),
        "files": sorted(task.files),
        "context": context,
        "input_hash": context_hash(context),
        "ai_result": ai_result,
        "updated_at": datetime.now().astimezone().isoformat(),
    }


def context_hash(context: dict[str, Any]) -> str:
    import hashlib

    payload = json.dumps(context, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def delta_context(current: dict[str, Any], previous_contexts: list[dict[str, Any]]) -> dict[str, Any]:
    previous_values: dict[str, set[str]] = {}
    for context in previous_contexts:
        for key in ("additional_requests", "latest_decisions", "process_evidence", "files"):
            previous_values.setdefault(key, set()).update(str(value) for value in context.get(key) or [])
        original = context.get("original_request")
        if original:
            previous_values.setdefault("original_request", set()).add(str(original))

    delta: dict[str, Any] = {
        "new_requests": [],
        "new_decisions": [],
        "new_process_evidence": [],
        "new_files": [],
    }
    original_request = current.get("original_request")
    if original_request and str(original_request) not in previous_values.get("original_request", set()):
        delta["new_requests"].append(str(original_request))
    delta["new_requests"].extend(diff_list(current.get("additional_requests"), previous_values.get("additional_requests", set())))
    delta["new_decisions"] = diff_list(current.get("latest_decisions"), previous_values.get("latest_decisions", set()))
    delta["new_process_evidence"] = diff_list(current.get("process_evidence"), previous_values.get("process_evidence", set()))
    delta["new_files"] = diff_list(current.get("files"), previous_values.get("files", set()))
    return delta


def diff_list(values: object, previous: set[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value) not in previous]


def ai_result_from_task(task: TaskSummary) -> dict[str, Any]:
    return {
        "title": task.ai_title,
        "request": task.ai_request,
        "decision": task.ai_decision,
        "outputs": task.ai_outputs,
        "deliverables": task.ai_deliverables,
        "impact": task.ai_impact,
        "evidence": task.ai_evidence,
        "artifact_paths": task.ai_artifact_paths,
        "next": task.ai_next,
        "next_actions": task.ai_next_actions,
        "blockers": task.ai_blockers,
        "questions": task.ai_questions,
        "validation_gaps": task.ai_validation_gaps,
        "owner_hint": task.ai_owner_hint,
    }


def apply_cached_result(task: TaskSummary, result: dict[str, Any]) -> None:
    task.ai_title = text_or_none(result.get("title"))
    task.ai_request = text_or_none(result.get("request"))
    task.ai_decision = text_or_none(result.get("decision"))
    task.ai_outputs = string_list(result.get("outputs"))
    task.ai_deliverables = string_list(result.get("deliverables"))
    if not task.ai_deliverables and task.ai_outputs:
        task.ai_deliverables = list(task.ai_outputs)
    task.ai_impact = text_or_none(result.get("impact"))
    task.ai_evidence = string_list(result.get("evidence"))
    task.ai_artifact_paths = string_list(result.get("artifact_paths"))
    task.ai_next = text_or_none(result.get("next"))
    task.ai_next_actions = string_list(result.get("next_actions"))
    task.ai_blockers = string_list(result.get("blockers"))
    task.ai_questions = string_list(result.get("questions"))
    task.ai_validation_gaps = string_list(result.get("validation_gaps"))
    task.ai_owner_hint = text_or_none(result.get("owner_hint"))


def text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
