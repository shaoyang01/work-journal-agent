from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .ai import call_deepseek_for_prompt, context_hash
from .ai_run_tracker import current_ai_run_id, finish_ai_task_item, start_ai_task_item
from .config import AppConfig
from .events import WorkEvent
from .merge import TaskSummary, repo_identity, strip_skill_invocations
from .sqlite_store import is_sqlite_storage, store_for
from .writers.obsidian import compact_items, relative_files


SEMANTIC_NAMESPACE = "event-semantics"
SEMANTIC_SCHEMA_VERSION = 1
MAX_EVENTS_PER_SEGMENT = 40
MAX_NODES_PER_REDUCTION = 6
MIN_TITLE_CONFIDENCE = 0.6
MAX_PARALLEL_SEMANTIC_CALLS = 4
SEMANTIC_CACHE_LOCK = threading.RLock()


@dataclass(frozen=True)
class SemanticSummaryResult:
    enabled: bool
    used: bool
    summarized: int
    message: str


def enrich_task_semantics(config: AppConfig, tasks: list[TaskSummary]) -> SemanticSummaryResult:
    if not config.ai.enabled:
        return SemanticSummaryResult(enabled=False, used=False, summarized=0, message="AI semantic summary disabled")
    if config.ai.provider != "deepseek":
        return SemanticSummaryResult(enabled=True, used=False, summarized=0, message=f"Unsupported AI provider: {config.ai.provider}")
    api_key = os.environ.get(config.ai.api_key_env)
    if not api_key:
        return SemanticSummaryResult(enabled=True, used=False, summarized=0, message=f"Missing API key env: {config.ai.api_key_env}")

    targets = [task for task in tasks if should_enrich_task(task)]
    run_id = current_ai_run_id()
    summarized = 0
    failures = 0
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_SEMANTIC_CALLS) as executor:
        futures = {executor.submit(summarize_task_recursively, config, api_key, task, run_id=run_id): task for task in targets}
        for future in as_completed(futures):
            task = futures[future]
            try:
                summary = future.result()
            except (OSError, ValueError, KeyError, TypeError, TimeoutError):
                failures += 1
                continue
            if apply_semantic_summary(task, summary):
                summarized += 1

    if summarized:
        suffix = f", {failures} failed" if failures else ""
        return SemanticSummaryResult(enabled=True, used=True, summarized=summarized, message=f"AI semantic summary applied ({summarized} task(s){suffix})")
    if failures:
        return SemanticSummaryResult(enabled=True, used=False, summarized=0, message=f"AI semantic summary failed ({failures} task(s))")
    return SemanticSummaryResult(enabled=True, used=False, summarized=0, message="AI semantic summary skipped")


def should_enrich_task(task: TaskSummary) -> bool:
    return task.event_count > 0


def summarize_task_recursively(config: AppConfig, api_key: str, task: TaskSummary, *, run_id: str | None = None) -> dict[str, Any]:
    contexts = event_contexts_for_task(task)
    if not contexts:
        return {}

    nodes: list[dict[str, Any]] = []
    segment_contexts = [
        {
            "task": task_descriptor(task),
            "segment_index": index,
            "events": chunk,
        }
        for index, chunk in enumerate(chunks(contexts, MAX_EVENTS_PER_SEGMENT))
    ]
    nodes = summarize_contexts_parallel(config, api_key, task.day, level="segment", contexts=segment_contexts, run_id=run_id)

    level = "session"
    while len(nodes) > 1:
        reduction_contexts = [
            {
                "task": task_descriptor(task),
                "reduction_index": index,
                "child_summaries": [summary_descriptor(node) for node in chunk],
            }
            for index, chunk in enumerate(chunks(nodes, MAX_NODES_PER_REDUCTION))
        ]
        nodes = summarize_contexts_parallel(config, api_key, task.day, level=level, contexts=reduction_contexts, run_id=run_id)
        level = "requirement"

    return nodes[0]


def summarize_contexts_parallel(
    config: AppConfig,
    api_key: str,
    day: date,
    *,
    level: str,
    contexts: list[dict[str, Any]],
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any] | None] = [None] * len(contexts)
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_SEMANTIC_CALLS) as executor:
        futures = {
            executor.submit(summarize_context, config, api_key, day, level=level, context=context, run_id=run_id): index
            for index, context in enumerate(contexts)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return [item for item in results if item is not None]


def summarize_context(config: AppConfig, api_key: str, day: date, *, level: str, context: dict[str, Any], run_id: str | None = None) -> dict[str, Any]:
    input_hash = context_hash({"schema": SEMANTIC_SCHEMA_VERSION, "level": level, "context": context})
    item_id, started = start_ai_task_item(
        config,
        run_id=run_id,
        phase="semantic",
        stage=level,
        batch_index=context_index(context),
        input_hash=input_hash,
        metadata=semantic_item_metadata(level=level, context=context),
    )
    try:
        cached = load_semantic_cache(config, day, input_hash)
        if cached:
            finish_ai_task_item(config, item_id, started, status="cached", result=semantic_item_result(cached))
            return cached

        payload = call_deepseek_for_prompt(
            config,
            api_key,
            build_semantic_prompt(level=level, context=context),
            timeout_seconds=min(config.ai.timeout_seconds, 90),
        )
        summary = clean_semantic_payload(payload, level=level, source_hash=input_hash)
        save_semantic_cache(config, day, input_hash, summary)
        finish_ai_task_item(config, item_id, started, status="succeeded", result=semantic_item_result(summary))
        return summary
    except Exception as exc:
        finish_ai_task_item(config, item_id, started, status="failed", error_message=str(exc))
        raise


def build_semantic_prompt(*, level: str, context: dict[str, Any]) -> str:
    return (
        "请从 Work Journal 工作事件上下文中提炼稳定的需求语义摘要。\n"
        "你不是在截断原文，而是在根据用户请求、agent 结论、文件锚点和时间上下文归纳这段工作到底是什么。\n"
        "要求：\n"
        "1. 只根据输入内容判断，不编造业务事实。\n"
        "2. title 必须是人能识别的需求标题，不能是文件路径、目录路径、SKILL.md 路径或原 prompt 截断。\n"
        "3. summary 用一句话说明这段工作的目标和范围。\n"
        "4. request 表达用户真正想做什么；outcome 表达已经得到的结论或产出。\n"
        "5. evidence 必须列出支撑标题的证据，例如用户请求、文件锚点、agent 结论。\n"
        "6. confidence 范围 0-1，证据不足时降低置信度。\n"
        "7. 输出必须是 JSON 对象，不要 Markdown，不要解释正文。\n"
        "JSON 字段：title,summary,request,outcome,objects,files,evidence,confidence。\n\n"
        f"level: {level}\n"
        + json.dumps(context, ensure_ascii=False)
    )


def clean_semantic_payload(payload: Any, *, level: str, source_hash: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("semantic summary response must be a JSON object")
    title = clean_text(payload.get("title"))
    confidence = numeric_confidence(payload.get("confidence"))
    return {
        "schema_version": SEMANTIC_SCHEMA_VERSION,
        "level": level,
        "input_hash": source_hash,
        "title": title,
        "summary": clean_text(payload.get("summary")),
        "request": clean_text(payload.get("request")),
        "outcome": clean_text(payload.get("outcome") or payload.get("decision")),
        "objects": clean_text_list(payload.get("objects"), limit=8, char_limit=80),
        "files": clean_text_list(payload.get("files"), limit=8, char_limit=120),
        "evidence": clean_text_list(payload.get("evidence"), limit=6, char_limit=160),
        "confidence": confidence,
        "title_valid": title_is_usable(title) and confidence >= MIN_TITLE_CONFIDENCE,
    }


def apply_semantic_summary(task: TaskSummary, summary: dict[str, Any]) -> bool:
    used = False
    title = clean_text(summary.get("title"))
    if summary.get("title_valid") and title:
        task.ai_title = title
        used = True
    request = clean_text(summary.get("request")) or clean_text(summary.get("summary"))
    if request and not task.ai_request:
        task.ai_request = request
        used = True
    outcome = clean_text(summary.get("outcome"))
    if outcome and not task.ai_decision:
        task.ai_decision = outcome
        used = True
    evidence = clean_text_list(summary.get("evidence"), limit=6, char_limit=160)
    if evidence:
        existing = list(task.ai_evidence)
        for item in evidence:
            if item not in existing:
                existing.append(item)
        task.ai_evidence = existing[:6]
        used = True
    files = clean_text_list(summary.get("files"), limit=8, char_limit=160)
    if files:
        existing_paths = list(task.ai_artifact_paths)
        for item in files:
            if item not in existing_paths:
                existing_paths.append(item)
        task.ai_artifact_paths = existing_paths[:8]
    return used


def event_contexts_for_task(task: TaskSummary) -> list[dict[str, Any]]:
    meaningful = [event for event in task.events if event_is_meaningful(event)]
    selected = meaningful or task.events
    contexts = [event_context(event) for event in selected]
    return [item for item in contexts if item]


def event_is_meaningful(event: WorkEvent) -> bool:
    text = " ".join([event.raw_request or "", event.summary or "", event.decision or ""]).strip()
    if not text:
        return False
    if text.lower() in {"ok", "okay", "yes", "y", "好", "好的", "继续"}:
        return False
    if event.event_type == "tool_result":
        return bool(event.raw_request or event.decision)
    return event.event_type in {"user_prompt", "note", "conclusion", "assistant_stop", "session_end"}


def event_context(event: WorkEvent) -> dict[str, Any]:
    summary = strip_skill_invocations(event.summary)
    raw_request = strip_skill_invocations(event.raw_request or "")
    decision = strip_skill_invocations(event.decision or "")
    return {
        "id": event.id,
        "time": event.occurred_at.isoformat(),
        "source": event.source,
        "type": event.event_type,
        "summary": truncate_text(summary, 260),
        "raw_request": truncate_text(raw_request, 420),
        "decision": truncate_text(decision, 320),
        "files": compact_items([path_hint(file) for file in event.files], limit=8, char_limit=120),
        "path_hints": path_hints_from_text(" ".join([summary, raw_request, decision])),
        "session_id": str(event.metadata.get("session_id") or ""),
        "branch": str(event.metadata.get("branch") or event.metadata.get("git_branch") or ""),
    }


def task_descriptor(task: TaskSummary) -> dict[str, Any]:
    return {
        "key": task.key,
        "current_title": strip_skill_invocations(task.title),
        "project": repo_identity(task.cwd),
        "cwd": task.cwd,
        "sources": sorted(task.sources),
        "event_count": task.event_count,
        "session_ids": sorted(task.session_ids),
        "branches": sorted(task.branches),
        "files": compact_items(relative_files(task), limit=12, char_limit=140),
    }


def summary_descriptor(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": summary.get("title") or "",
        "summary": summary.get("summary") or "",
        "request": summary.get("request") or "",
        "outcome": summary.get("outcome") or "",
        "objects": summary.get("objects") or [],
        "files": summary.get("files") or [],
        "evidence": summary.get("evidence") or [],
        "confidence": summary.get("confidence") or 0,
    }


def context_index(context: dict[str, Any]) -> int | None:
    if "segment_index" in context:
        return int(context["segment_index"])
    if "reduction_index" in context:
        return int(context["reduction_index"])
    return None


def semantic_item_metadata(*, level: str, context: dict[str, Any]) -> dict[str, Any]:
    task = context.get("task") if isinstance(context.get("task"), dict) else {}
    events = context.get("events") if isinstance(context.get("events"), list) else []
    child_summaries = context.get("child_summaries") if isinstance(context.get("child_summaries"), list) else []
    return {
        "level": level,
        "task_key": task.get("key"),
        "title": task.get("current_title"),
        "project": task.get("project"),
        "event_count": len(events),
        "child_count": len(child_summaries),
    }


def semantic_item_result(summary: dict[str, Any]) -> dict[str, Any]:
    evidence = summary.get("evidence") if isinstance(summary.get("evidence"), list) else []
    return {
        "title": summary.get("title") or "",
        "request": summary.get("request") or "",
        "outcome": summary.get("outcome") or "",
        "confidence": summary.get("confidence") or 0,
        "title_valid": bool(summary.get("title_valid")),
        "evidence_count": len(evidence),
    }


def load_semantic_cache(config: AppConfig, day: date, input_hash: str) -> dict[str, Any]:
    payload = load_semantic_day_cache(config, day)
    item = payload.get("items", {}).get(input_hash)
    return item if isinstance(item, dict) else {}


def save_semantic_cache(config: AppConfig, day: date, input_hash: str, summary: dict[str, Any]) -> None:
    if not config.ai.cache_enabled:
        return
    with SEMANTIC_CACHE_LOCK:
        payload = load_semantic_day_cache(config, day)
        items = payload.setdefault("items", {})
        if isinstance(items, dict):
            items[input_hash] = summary
        payload["schema_version"] = SEMANTIC_SCHEMA_VERSION
        payload["date"] = day.isoformat()
        save_semantic_day_cache(config, day, payload)


def load_semantic_day_cache(config: AppConfig, day: date) -> dict[str, Any]:
    with SEMANTIC_CACHE_LOCK:
        if not config.ai.cache_enabled:
            return {"schema_version": SEMANTIC_SCHEMA_VERSION, "date": day.isoformat(), "items": {}}
        if is_sqlite_storage(config.storage):
            store = store_for(config.storage)
            with store.connect() as conn:
                payload = store.load_ai_cache(conn, SEMANTIC_NAMESPACE, day)
            if isinstance(payload, dict) and payload.get("schema_version") == SEMANTIC_SCHEMA_VERSION:
                payload.setdefault("items", {})
                return payload
            return {"schema_version": SEMANTIC_SCHEMA_VERSION, "date": day.isoformat(), "items": {}}
        path = semantic_cache_path(config, day)
        if not path.exists():
            return {"schema_version": SEMANTIC_SCHEMA_VERSION, "date": day.isoformat(), "items": {}}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": SEMANTIC_SCHEMA_VERSION, "date": day.isoformat(), "items": {}}
        if isinstance(payload, dict) and payload.get("schema_version") == SEMANTIC_SCHEMA_VERSION:
            payload.setdefault("items", {})
            return payload
        return {"schema_version": SEMANTIC_SCHEMA_VERSION, "date": day.isoformat(), "items": {}}


def save_semantic_day_cache(config: AppConfig, day: date, payload: dict[str, Any]) -> None:
    with SEMANTIC_CACHE_LOCK:
        if is_sqlite_storage(config.storage):
            store = store_for(config.storage)
            with store.connect() as conn:
                store.save_ai_cache(conn, SEMANTIC_NAMESPACE, day, payload)
            return
        path = semantic_cache_path(config, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def semantic_cache_path(config: AppConfig, day: date) -> Path:
    return config.ai.cache_dir / SEMANTIC_NAMESPACE / f"{day.isoformat()}.json"


def title_is_usable(value: object) -> bool:
    title = clean_text(value)
    if not title or len(title) < 4:
        return False
    lowered = title.lower()
    if "$speckit" in lowered or "speckit-" in lowered:
        return False
    if "/users/" in lowered or "\\users\\" in lowered or "/.codex/" in lowered or "/skills/" in lowered:
        return False
    if "skill.md" in lowered or lowered.endswith((".java", ".py", ".md", ".json", ".xml", ".yaml", ".yml", ".toml", ".sql")):
        return False
    if title.startswith("@") or "/" in title or "\\" in title:
        return False
    separators = sum(1 for char in title if char in "/\\._-")
    if separators / max(1, len(title)) > 0.18:
        return False
    if re.fullmatch(r"[\w.-]+\.(java|py|md|json|xml|yaml|yml|toml|sql)", title, re.I):
        return False
    return True


def path_hints_from_text(value: str) -> list[str]:
    hints: list[str] = []
    for match in re.finditer(r"([~@/]?[A-Za-z0-9_./@-]+/(?:[A-Za-z0-9_.@-]+/)*[A-Za-z0-9_.@-]+)", value):
        hint = path_hint(match.group(1))
        if hint and hint not in hints:
            hints.append(hint)
        if len(hints) >= 8:
            break
    return hints


def path_hint(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text.lstrip("@"))
    parts = path.parts
    lowered = text.lower()
    if "skill.md" in lowered and len(parts) >= 2:
        return f"skill:{parts[-2]}"
    return path.name or text


def chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def clean_text_list(value: object, *, limit: int, char_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = truncate_text(clean_text(item), char_limit)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
        if len(result) >= limit:
            break
    return result


def truncate_text(value: str, limit: int) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def numeric_confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
