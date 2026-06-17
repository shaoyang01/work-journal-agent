from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .ai_cache import context_hash
from .ai_run_tracker import cleanup_stale_task_runs, current_ai_run_id, finish_ai_task_item, start_ai_task_item, track_ai_task_run
from .ai import call_deepseek_for_prompt, review_task_clusters
from .config import AppConfig, default_data_dir
from .event_semantics import enrich_task_semantics
from .events import WorkEvent, keywords, read_events
from .merge import TaskSummary, group_events, repo_identity, task_from_events
from .sqlite_store import is_sqlite_storage, store_for
from .writers.obsidian import compact_items, display_title, relative_files

ACTIVE_REQUIREMENT_STATUSES = {"in_progress", "paused"}
MANUAL_REQUIREMENT_ID_PREFIX = "new_"
INCREMENTAL_MERGE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReviewPaths:
    root: Path
    threads: Path
    daily_dir: Path
    state_dir: Path


def requirement_paths() -> ReviewPaths:
    root = default_data_dir() / "requirements"
    return ReviewPaths(
        root=root,
        threads=root / "threads.json",
        daily_dir=root / "daily",
        state_dir=default_data_dir() / "state",
    )


def daily_review_path(day: date) -> Path:
    return requirement_paths().daily_dir / f"{day.isoformat()}.json"


def status_path() -> Path:
    return requirement_paths().state_dir / "status.json"


def build_review_payload(config: AppConfig, day: date) -> dict[str, Any]:
    return refresh_requirement_candidates(config, day)


def refresh_requirement_candidates(config: AppConfig, day: date, *, fail_if_active: bool = False) -> dict[str, Any]:
    events = read_events(config.storage, day=day)
    existing_daily = load_daily_review(day, storage=config.storage)
    existing_candidates = [item for item in existing_daily.get("candidates") or [] if isinstance(item, dict)]
    ignored_event_ids = set(existing_daily.get("ignored_event_ids") or [])
    known_event_ids = known_candidate_event_ids(existing_daily)
    incremental = bool(existing_candidates)
    events_to_process = [event for event in events if event.id not in known_event_ids and event.id not in ignored_event_ids] if incremental else events

    if incremental and not events_to_process:
        threads = load_threads(storage=config.storage)
        candidates = apply_saved_assignments_to_candidates(existing_candidates, existing_daily)
        payload = build_daily_review_payload(
            day=day,
            candidates=candidates,
            threads=threads,
            existing_daily=existing_daily,
            ignored_event_ids=ignored_event_ids,
            summary={
                **summary_dict(existing_daily),
                "event_count": sum(candidate_event_count(candidate) for candidate in candidates),
                "semantic_summary": "No new requirement events",
                "cluster_review": "AI cluster review skipped",
                "incremental_merge": "No new requirement events",
            },
        )
        save_daily_review_payload(day, payload, storage=config.storage)
        write_status(day=day, pending_count=payload["summary"]["pending_candidates"], daily_path=daily_note_path(config, day), storage=config.storage)
        return payload

    tasks = group_events(events_to_process, min_keyword_overlap=config.merge.min_keyword_overlap)
    with track_ai_task_run(
        config,
        day=day,
        run_kind="requirement_review",
        metadata={
            "event_count": len(events),
            "new_event_count": len(events_to_process),
            "existing_candidate_count": len(existing_candidates),
            "local_task_count": len(tasks),
            "mode": "incremental" if incremental else "full",
        },
        fail_if_active=fail_if_active,
    ):
        semantic_result = enrich_task_semantics(config, tasks)
        cluster_result = review_task_clusters(config, tasks)
        tasks = cluster_result.tasks
        new_candidates = [candidate_from_task(task, existing_daily={} if incremental else existing_daily) for task in tasks if task.event_count > 0]
        if incremental:
            candidates, assignments, incremental_message = merge_incremental_requirement_candidates(
                config,
                day=day,
                existing_daily=existing_daily,
                existing_candidates=apply_saved_assignments_to_candidates(existing_candidates, existing_daily),
                new_candidates=new_candidates,
            )
        else:
            candidates = new_candidates
            assignments = [item for item in existing_daily.get("assignments") or [] if isinstance(item, dict)]
            incremental_message = "Requirement candidates initialized"
    threads = load_threads(storage=config.storage)
    payload = build_daily_review_payload(
        day=day,
        candidates=candidates,
        threads=threads,
        existing_daily=existing_daily,
        ignored_event_ids=ignored_event_ids,
        assignments=assignments,
        summary={
            "event_count": sum(candidate_event_count(candidate) for candidate in candidates),
            "semantic_summary": semantic_result.message,
            "cluster_review": cluster_result.message,
            "incremental_merge": incremental_message,
        },
    )
    save_daily_review_payload(day, payload, storage=config.storage)
    write_status(day=day, pending_count=payload["summary"]["pending_candidates"], daily_path=daily_note_path(config, day), storage=config.storage)
    return payload


def load_review_payload(config: AppConfig, day: date) -> dict[str, Any]:
    existing_daily = load_daily_review(day, storage=config.storage)
    threads = load_threads(storage=config.storage)
    candidates = [item for item in existing_daily.get("candidates") or [] if isinstance(item, dict)]
    candidates = apply_saved_assignments_to_candidates(candidates, existing_daily)
    pending = [candidate for candidate in candidates if candidate.get("status") not in {"confirmed", "ignored"}]
    payload = {
        "date": day.isoformat(),
        "generated_at": str(existing_daily.get("generated_at") or existing_daily.get("updated_at") or now_iso()),
        "updated_at": str(existing_daily.get("updated_at") or ""),
        "candidates": candidates,
        "requirements": requirement_options(threads, active_only=True),
        "ignored_event_ids": sorted(set(str(value) for value in existing_daily.get("ignored_event_ids") or [] if value)),
        "assignments": [item for item in existing_daily.get("assignments") or [] if isinstance(item, dict)],
        "summary": {
            **(existing_daily.get("summary") if isinstance(existing_daily.get("summary"), dict) else {}),
            "total_candidates": len(candidates),
            "pending_candidates": len(pending),
        },
    }
    write_status(day=day, pending_count=len(pending), daily_path=daily_note_path(config, day), storage=config.storage)
    return payload


def apply_saved_assignments_to_candidates(candidates: list[dict[str, Any]], existing_daily: dict[str, Any]) -> list[dict[str, Any]]:
    assignments_by_candidate = {
        str(assignment.get("candidate_id")): assignment
        for assignment in existing_daily.get("assignments") or []
        if isinstance(assignment, dict) and assignment.get("candidate_id")
    }
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        assignment = assignments_by_candidate.get(str(item.get("candidate_id") or ""))
        if assignment:
            for key in ("requirement_id", "title", "project", "requirement_type", "status", "event_ids", "anchors"):
                if key in assignment:
                    if key == "event_ids":
                        item[key] = sorted(set(str(value) for value in item.get("event_ids") or [] if value) | set(str(value) for value in assignment.get("event_ids") or [] if value))
                    elif key == "anchors":
                        item[key] = merge_anchors(item.get("anchors"), assignment.get("anchors"))
                    else:
                        item[key] = assignment[key]
        result.append(item)
    return result


def build_daily_review_payload(
    *,
    day: date,
    candidates: list[dict[str, Any]],
    threads: dict[str, dict[str, Any]],
    existing_daily: dict[str, Any],
    ignored_event_ids: set[str],
    assignments: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pending = [candidate for candidate in candidates if candidate.get("status") not in {"confirmed", "ignored"}]
    return {
        "date": day.isoformat(),
        "generated_at": str(existing_daily.get("generated_at") or now_iso()),
        "updated_at": now_iso(),
        "candidates": candidates,
        "requirements": requirement_options(threads, active_only=True),
        "ignored_event_ids": sorted(ignored_event_ids),
        "assignments": assignments if assignments is not None else [item for item in existing_daily.get("assignments") or [] if isinstance(item, dict)],
        "summary": {
            **(summary or {}),
            "total_candidates": len(candidates),
            "pending_candidates": len(pending),
        },
    }


def summary_dict(existing_daily: dict[str, Any]) -> dict[str, Any]:
    summary = existing_daily.get("summary")
    return dict(summary) if isinstance(summary, dict) else {}


def known_candidate_event_ids(existing_daily: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for collection_name in ("candidates", "assignments"):
        for item in existing_daily.get(collection_name) or []:
            if not isinstance(item, dict):
                continue
            result.update(str(value) for value in item.get("event_ids") or [] if value)
    result.update(str(value) for value in existing_daily.get("ignored_event_ids") or [] if value)
    return result


def candidate_event_count(candidate: dict[str, Any]) -> int:
    event_ids = [value for value in candidate.get("event_ids") or [] if value]
    return len(event_ids)


def merge_incremental_requirement_candidates(
    config: AppConfig,
    *,
    day: date,
    existing_daily: dict[str, Any],
    existing_candidates: list[dict[str, Any]],
    new_candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    if not new_candidates:
        assignments = sync_assignments_with_candidates(existing_daily, existing_candidates)
        return existing_candidates, assignments, "No new requirement candidates"
    operations, message = plan_incremental_requirement_merge(config, day=day, existing_candidates=existing_candidates, new_candidates=new_candidates)
    merged_candidates, stats = apply_incremental_merge_operations(existing_candidates, new_candidates, operations)
    assignments = sync_assignments_with_candidates(existing_daily, merged_candidates)
    return merged_candidates, assignments, f"{message} ({stats['appended']} appended, {stats['created']} new)"


def plan_incremental_requirement_merge(
    config: AppConfig,
    *,
    day: date,
    existing_candidates: list[dict[str, Any]],
    new_candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    if not existing_candidates:
        return create_new_operations(new_candidates), "Incremental requirement merge initialized"
    if not config.ai.enabled:
        return fallback_incremental_merge_operations(existing_candidates, new_candidates), "Incremental requirement merge used local fallback"
    if config.ai.provider != "deepseek":
        return create_new_operations(new_candidates), f"Incremental requirement merge skipped unsupported provider: {config.ai.provider}"
    api_key = os.environ.get(config.ai.api_key_env)
    if not api_key:
        return create_new_operations(new_candidates), f"Incremental requirement merge skipped missing API key env: {config.ai.api_key_env}"

    context = {
        "schema_version": INCREMENTAL_MERGE_SCHEMA_VERSION,
        "date": day.isoformat(),
        "existing_candidates": [incremental_candidate_context(candidate, existing=True) for candidate in existing_candidates],
        "new_candidates": [incremental_candidate_context(candidate, existing=False) for candidate in new_candidates],
    }
    input_hash = context_hash(context)
    item_id, started = start_ai_task_item(
        config,
        run_id=current_ai_run_id(),
        phase="requirement_merge",
        stage="incremental",
        batch_index=0,
        input_hash=input_hash,
        metadata={
            "existing_candidate_count": len(existing_candidates),
            "new_candidate_count": len(new_candidates),
        },
    )
    try:
        payload = call_deepseek_for_prompt(
            config,
            api_key,
            build_incremental_requirement_merge_prompt(context),
            timeout_seconds=config.ai.cluster_review_timeout_seconds,
        )
        operations = clean_incremental_merge_operations(payload, new_candidates)
        finish_ai_task_item(config, item_id, started, status="succeeded", result={"operation_count": len(operations)})
        return operations, "Incremental requirement merge applied by DeepSeek"
    except Exception as exc:
        finish_ai_task_item(config, item_id, started, status="failed", error_message=str(exc))
        return create_new_operations(new_candidates), f"Incremental requirement merge failed, kept new candidates separate: {exc}"


def build_incremental_requirement_merge_prompt(context: dict[str, Any]) -> str:
    return (
        "请判断新增候选需求是否应合并到已有候选需求中。\n"
        "要求：\n"
        "1. 只处理 new_candidates 中的 candidate_id。\n"
        "2. 如果新增候选明显延续某个已有候选，输出 action=append_to_existing，并填写 target_candidate_id 和 new_candidate_ids。\n"
        "3. 如果新增候选是新需求，输出 action=create_new_candidate，并填写 new_candidate_ids。\n"
        "4. 不要修改已有候选的 candidate_id；不要把低置信度内容硬合并。\n"
        "5. 每个 new_candidate_id 必须且只能出现一次。\n"
        "6. 输出必须是 JSON 对象，字段 operations 为数组；每项字段：action,target_candidate_id,new_candidate_ids,confidence,reason。\n\n"
        + json.dumps(context, ensure_ascii=False)
    )


def clean_incremental_merge_operations(payload: Any, new_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("incremental requirement merge response must be a JSON object")
    operations = payload.get("operations")
    if not isinstance(operations, list):
        raise ValueError("incremental requirement merge response must contain operations")
    valid_new_ids = {str(candidate.get("candidate_id") or "") for candidate in new_candidates}
    result: list[dict[str, Any]] = []
    consumed: set[str] = set()
    for item in operations:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        new_ids = [str(value) for value in item.get("new_candidate_ids") or [] if str(value) in valid_new_ids]
        new_ids = [value for value in dict.fromkeys(new_ids) if value not in consumed]
        if action not in {"append_to_existing", "create_new_candidate"} or not new_ids:
            continue
        consumed.update(new_ids)
        result.append(
            {
                "action": action,
                "target_candidate_id": str(item.get("target_candidate_id") or "").strip(),
                "new_candidate_ids": new_ids,
                "confidence": numeric_confidence(item.get("confidence")),
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    for candidate in new_candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        if candidate_id and candidate_id not in consumed:
            result.append({"action": "create_new_candidate", "target_candidate_id": "", "new_candidate_ids": [candidate_id], "confidence": 0.0, "reason": "未被模型归并，保留为新候选。"})
    return result


def apply_incremental_merge_operations(
    existing_candidates: list[dict[str, Any]],
    new_candidates: list[dict[str, Any]],
    operations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    existing_by_id = {str(candidate.get("candidate_id") or ""): dict(candidate) for candidate in existing_candidates if candidate.get("candidate_id")}
    new_by_id = {str(candidate.get("candidate_id") or ""): dict(candidate) for candidate in new_candidates if candidate.get("candidate_id")}
    consumed: set[str] = set()
    appended = 0
    created = 0
    for operation in operations:
        new_ids = [candidate_id for candidate_id in operation.get("new_candidate_ids") or [] if candidate_id in new_by_id and candidate_id not in consumed]
        if not new_ids:
            continue
        if operation.get("action") == "append_to_existing":
            target = existing_by_id.get(str(operation.get("target_candidate_id") or ""))
            if target is None or numeric_confidence(operation.get("confidence")) < 0.65:
                continue
            for new_id in new_ids:
                append_candidate(target, new_by_id[new_id], reason=str(operation.get("reason") or ""))
                consumed.add(new_id)
                appended += 1
            continue
        if operation.get("action") == "create_new_candidate":
            for new_id in new_ids:
                consumed.add(new_id)
                created += 1

    result = list(existing_by_id.values())
    for candidate in new_candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        if candidate_id and candidate_id not in consumed:
            result.append(dict(candidate))
            created += 1
        elif candidate_id in consumed and any(candidate_id in operation.get("new_candidate_ids", []) and operation.get("action") == "create_new_candidate" for operation in operations):
            result.append(dict(candidate))
    result.sort(key=candidate_sort_key)
    return result, {"appended": appended, "created": created}


def append_candidate(target: dict[str, Any], source: dict[str, Any], *, reason: str) -> None:
    target["event_ids"] = sorted(set(str(value) for value in target.get("event_ids") or [] if value) | set(str(value) for value in source.get("event_ids") or [] if value))
    target["event_count"] = len(target["event_ids"])
    target["sources"] = sorted(set(str(value) for value in target.get("sources") or [] if value) | set(str(value) for value in source.get("sources") or [] if value))
    target["anchors"] = merge_anchors(target.get("anchors"), source.get("anchors"))
    target["files"] = compact_items([*list(target.get("files") or []), *list(source.get("files") or [])], limit=8, char_limit=140)
    if not str(target.get("request") or "").strip() and source.get("request"):
        target["request"] = source.get("request")
    if source.get("decision"):
        target["decision"] = source.get("decision")
    target["confidence"] = max(numeric_confidence(target.get("confidence")), numeric_confidence(source.get("confidence")))
    reasons = [str(value) for value in target.get("reasons") or [] if value]
    merge_reason = reason or f"新增 {candidate_event_count(source)} 条事件归并到该需求。"
    if merge_reason and merge_reason not in reasons:
        reasons.append(merge_reason)
    target["reasons"] = compact_items(reasons, limit=5, char_limit=160)


def sync_assignments_with_candidates(existing_daily: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates_by_id = {str(candidate.get("candidate_id") or ""): candidate for candidate in candidates}
    result: list[dict[str, Any]] = []
    for assignment in existing_daily.get("assignments") or []:
        if not isinstance(assignment, dict):
            continue
        candidate = candidates_by_id.get(str(assignment.get("candidate_id") or ""))
        if not candidate:
            result.append(assignment)
            continue
        result.append(
            {
                **assignment,
                "event_ids": [str(value) for value in candidate.get("event_ids") or [] if value],
                "anchors": merge_anchors(assignment.get("anchors"), candidate.get("anchors")),
            }
        )
    return result


def fallback_incremental_merge_operations(existing_candidates: list[dict[str, Any]], new_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for candidate in new_candidates:
        target = best_local_merge_target(existing_candidates, candidate)
        if target:
            operations.append(
                {
                    "action": "append_to_existing",
                    "target_candidate_id": target,
                    "new_candidate_ids": [str(candidate.get("candidate_id") or "")],
                    "confidence": 0.7,
                    "reason": "本地关键词和项目匹配，归并到已有候选。",
                }
            )
        else:
            operations.append({"action": "create_new_candidate", "target_candidate_id": "", "new_candidate_ids": [str(candidate.get("candidate_id") or "")], "confidence": 0.0, "reason": "未找到可靠已有候选。"})
    return operations


def best_local_merge_target(existing_candidates: list[dict[str, Any]], candidate: dict[str, Any]) -> str:
    project = str(candidate.get("project") or "")
    text = candidate_match_text(candidate)
    words = keywords(text)
    best_id = ""
    best_score = 0
    for existing in existing_candidates:
        if str(existing.get("project") or "") != project:
            continue
        existing_words = keywords(candidate_match_text(existing))
        score = len(words.intersection(existing_words))
        if score > best_score:
            best_score = score
            best_id = str(existing.get("candidate_id") or "")
    return best_id if best_score >= 2 else ""


def candidate_match_text(candidate: dict[str, Any]) -> str:
    return " ".join(str(candidate.get(key) or "") for key in ("title", "suggested_title", "request", "decision"))


def create_new_operations(new_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "action": "create_new_candidate",
            "target_candidate_id": "",
            "new_candidate_ids": [str(candidate.get("candidate_id") or "")],
            "confidence": 0.0,
            "reason": "保留为新候选。",
        }
        for candidate in new_candidates
        if candidate.get("candidate_id")
    ]


def incremental_candidate_context(candidate: dict[str, Any], *, existing: bool) -> dict[str, Any]:
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "requirement_id": str(candidate.get("requirement_id") or ""),
        "title": str(candidate.get("title") or ""),
        "suggested_title": str(candidate.get("suggested_title") or ""),
        "project": str(candidate.get("project") or ""),
        "status": str(candidate.get("status") or ""),
        "request": str(candidate.get("request") or ""),
        "decision": str(candidate.get("decision") or ""),
        "event_count": candidate_event_count(candidate),
        "event_ids": [str(value) for value in candidate.get("event_ids") or [] if value],
        "files": [str(value) for value in candidate.get("files") or [] if value][:8],
        "anchors": candidate.get("anchors") if isinstance(candidate.get("anchors"), dict) else {},
        "kind": "existing" if existing else "new",
    }


def candidate_sort_key(candidate: dict[str, Any]) -> tuple[str, str]:
    event_ids = [str(value) for value in candidate.get("event_ids") or [] if value]
    return (event_ids[0] if event_ids else "", str(candidate.get("candidate_id") or ""))


def numeric_confidence(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def filter_ignored_events(day: date, events: list[WorkEvent], *, storage: Any | None = None) -> list[WorkEvent]:
    ignored_event_ids = ignored_event_ids_for_day(day, storage=storage)
    if not ignored_event_ids:
        return events
    return [event for event in events if event.id not in ignored_event_ids]


def ignored_event_ids_for_day(day: date, *, storage: Any | None = None) -> set[str]:
    daily_payload = load_daily_review(day, storage=storage)
    return {str(value) for value in daily_payload.get("ignored_event_ids") or [] if value}


def candidate_from_task(task: TaskSummary, *, existing_daily: dict[str, Any]) -> dict[str, Any]:
    candidate_id = candidate_id_for_task(task)
    existing = assignment_for_candidate(existing_daily, candidate_id)
    requirement_id = str(existing.get("requirement_id") or "").strip()
    title = str(existing.get("title") or display_title(task))
    project = repo_identity(task.cwd) or "unknown"
    anchors = anchors_for_task(task)
    confidence = confidence_for_task(task, title=title, anchors=anchors)
    status = str(existing.get("status") or ("pending" if confidence < 0.85 else "suggested"))
    return {
        "candidate_id": candidate_id,
        "requirement_id": requirement_id,
        "title": title,
        "suggested_title": display_title(task),
        "project": project,
        "requirement_type": str(existing.get("requirement_type") or infer_requirement_type(task)),
        "status": status,
        "confidence": confidence,
        "event_ids": sorted(task.event_ids),
        "event_count": task.event_count,
        "sources": sorted(task.sources),
        "anchors": anchors,
        "request": task.ai_request or first_non_empty(task.raw_requests),
        "decision": task.ai_decision or first_non_empty(reversed(task.decisions)),
        "files": compact_items(relative_files(task), limit=8, char_limit=140),
        "reasons": reasons_for_task(task, title=title, anchors=anchors),
    }


def candidate_id_for_task(task: TaskSummary) -> str:
    seed = "|".join([task.day.isoformat(), repo_identity(task.cwd), *sorted(task.event_ids)])
    return "cand_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def assignment_for_candidate(existing_daily: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    for assignment in existing_daily.get("assignments") or []:
        if isinstance(assignment, dict) and assignment.get("candidate_id") == candidate_id:
            return assignment
    return {}


def anchors_for_task(task: TaskSummary) -> dict[str, list[str]]:
    files = relative_files(task)
    plan_docs: list[str] = []
    review_docs: list[str] = []
    requirement_docs: list[str] = []
    implementation_files: list[str] = []
    for path in files:
        lowered = path.lower()
        if "/.claude/plans/" in lowered or "claude/plans" in lowered or lowered.endswith(".md") and "plan" in lowered:
            plan_docs.append(path)
        elif "review" in lowered or "审查" in path or "review" in path:
            review_docs.append(path)
        elif "需求文档" in path or "技术文档" in path or lowered.endswith((".html", ".md")):
            requirement_docs.append(path)
        elif lowered.endswith((".java", ".kt", ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".sql", ".xml", ".yml", ".yaml")):
            implementation_files.append(path)
    return {
        "plan_docs": compact_items(plan_docs, limit=5, char_limit=180),
        "review_docs": compact_items(review_docs, limit=5, char_limit=180),
        "requirement_docs": compact_items(requirement_docs, limit=5, char_limit=180),
        "implementation_files": compact_items(implementation_files, limit=8, char_limit=180),
    }


def confidence_for_task(task: TaskSummary, *, title: str, anchors: dict[str, list[str]]) -> float:
    score = 0.55
    if task.ai_title:
        score += 0.12
    if task.raw_requests:
        score += 0.08
    if anchors.get("plan_docs") or anchors.get("requirement_docs"):
        score += 0.12
    if anchors.get("implementation_files"):
        score += 0.08
    if task.event_count >= 30:
        score -= 0.08
    if looks_like_file_or_prompt_title(title):
        score -= 0.25
    return max(0.05, min(0.98, round(score, 2)))


def looks_like_file_or_prompt_title(title: str) -> bool:
    lowered = title.lower()
    return (
        "/" in title
        or title.startswith("@")
        or lowered.endswith((".java", ".py", ".md", ".html", ".json", ".xml"))
        or "src/main/" in lowered
    )


def reasons_for_task(task: TaskSummary, *, title: str, anchors: dict[str, list[str]]) -> list[str]:
    reasons: list[str] = []
    if looks_like_file_or_prompt_title(title):
        reasons.append("标题像文件路径或对话引用，建议人工改名。")
    if task.event_count >= 30:
        reasons.append(f"事件数较多（{task.event_count} 条），可能跨越多个阶段。")
    if anchors.get("plan_docs"):
        reasons.append("发现方案文档锚点。")
    if anchors.get("review_docs"):
        reasons.append("发现 review/审查报告锚点。")
    if anchors.get("implementation_files"):
        reasons.append("发现实现文件锚点。")
    return reasons or ["按日期、项目、文件和会话初步聚合。"]


def infer_requirement_type(task: TaskSummary) -> str:
    text = " ".join([task.title, *task.raw_requests, *task.discussions, *task.decisions]).lower()
    if "review" in text or "审查" in text:
        return "review"
    if "排查" in text or "debug" in text or "问题" in text:
        return "debug"
    if "方案" in text or "文档" in text:
        return "plan-driven"
    return "direct"


def first_non_empty(items: Any) -> str:
    for item in items:
        text = " ".join(str(item).split())
        if text:
            return text
    return ""


def save_review_decisions(day: date, decisions: list[dict[str, Any]], *, config: AppConfig) -> dict[str, Any]:
    threads = load_threads(storage=config.storage)
    existing_daily = load_daily_review(day, storage=config.storage)
    saved_assignments: list[dict[str, Any]] = []
    ignored_event_ids: list[str] = []
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        status = str(decision.get("status") or "pending")
        event_ids = [str(value) for value in decision.get("event_ids") or [] if value]
        if status == "ignored":
            ignored_event_ids.extend(event_ids)
            saved_assignments.append(normalize_assignment(decision, requirement_id=""))
            continue
        if status != "confirmed":
            saved_assignments.append(normalize_assignment(decision, requirement_id=""))
            continue
        selected_requirement_id = str(decision.get("requirement_id") or "").strip()
        selected_thread = threads.get(selected_requirement_id)
        if selected_thread:
            normalized_decision = {
                **decision,
                "title": str(selected_thread.get("title") or decision.get("title") or "").strip(),
                "project": str(selected_thread.get("project") or decision.get("project") or "unknown").strip() or "unknown",
                "requirement_type": str(selected_thread.get("type") or decision.get("requirement_type") or "direct"),
            }
            requirement_id = selected_requirement_id
        else:
            normalized_decision = decision
            title = str(normalized_decision.get("title") or "").strip()
            project = str(normalized_decision.get("project") or "unknown").strip() or "unknown"
            requirement_id = requirement_id_for(project=project, title=title)
        upsert_thread(threads, requirement_id=requirement_id, decision=normalized_decision)
        saved_assignments.append(normalize_assignment(normalized_decision, requirement_id=requirement_id))
    daily_payload = {
        **existing_daily,
        "date": day.isoformat(),
        "updated_at": now_iso(),
        "assignments": saved_assignments,
        "ignored_event_ids": sorted(set(ignored_event_ids)),
    }
    if is_sqlite_storage(config.storage):
        persist_threads(threads, storage=config.storage)
        store = store_for(config.storage)
        with store.connect() as conn:
            store.save_daily_review(conn, day, daily_payload)
    else:
        paths = requirement_paths()
        paths.daily_dir.mkdir(parents=True, exist_ok=True)
        persist_threads(threads, storage=config.storage)
        save_daily_review_payload(day, daily_payload, storage=config.storage)
    pending_count = len([item for item in saved_assignments if item.get("status") not in {"confirmed", "ignored"}])
    write_status(day=day, pending_count=pending_count, daily_path=daily_note_path(config, day), storage=config.storage)
    return daily_payload


def normalize_assignment(decision: dict[str, Any], *, requirement_id: str) -> dict[str, Any]:
    return {
        "candidate_id": str(decision.get("candidate_id") or ""),
        "requirement_id": requirement_id,
        "title": str(decision.get("title") or "").strip(),
        "project": str(decision.get("project") or "unknown").strip() or "unknown",
        "requirement_type": str(decision.get("requirement_type") or "direct"),
        "status": str(decision.get("status") or "pending"),
        "event_ids": [str(value) for value in decision.get("event_ids") or [] if value],
        "anchors": decision.get("anchors") if isinstance(decision.get("anchors"), dict) else {},
    }


def requirement_options(threads: dict[str, dict[str, Any]], *, active_only: bool = False) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for thread in threads.values():
        if not isinstance(thread, dict):
            continue
        status = normalize_thread_status(thread.get("status"))
        if active_only and status not in ACTIVE_REQUIREMENT_STATUSES:
            continue
        requirement_id = str(thread.get("id") or "").strip()
        title = str(thread.get("title") or "").strip()
        if not requirement_id or not title:
            continue
        result.append(
            {
                "id": requirement_id,
                "title": title,
                "project": str(thread.get("project") or "unknown"),
                "requirement_type": str(thread.get("type") or "direct"),
                "status": status,
                "note": str(thread.get("note") or ""),
                "created_at": str(thread.get("created_at") or ""),
                "updated_at": str(thread.get("updated_at") or ""),
            }
        )
    result.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return result


def build_requirement_management_payload(config: AppConfig) -> dict[str, Any]:
    threads = load_threads(storage=config.storage)
    requirements = requirement_options(threads, active_only=False)
    return {
        "generated_at": now_iso(),
        "requirements": requirements,
        "summary": {
            "total": len(requirements),
            "active": len([item for item in requirements if item.get("status") in ACTIVE_REQUIREMENT_STATUSES]),
            "completed": len([item for item in requirements if item.get("status") == "completed"]),
        },
    }


def save_requirement_threads(items: list[dict[str, Any]], *, config: AppConfig) -> dict[str, Any]:
    threads = load_threads(storage=config.storage)
    now = now_iso()
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        project = str(item.get("project") or "unknown").strip() or "unknown"
        if not title:
            continue
        raw_id = str(item.get("id") or "").strip()
        if raw_id and not raw_id.startswith(MANUAL_REQUIREMENT_ID_PREFIX):
            requirement_id = raw_id
        else:
            requirement_id = requirement_id_for(project=project, title=title)
        existing = threads.get(requirement_id, {})
        threads[requirement_id] = {
            "id": requirement_id,
            "title": title,
            "project": project,
            "type": str(item.get("requirement_type") or existing.get("type") or "direct"),
            "status": normalize_thread_status(item.get("status") or existing.get("status")),
            "note": str(item.get("note") or "").strip(),
            "anchors": existing.get("anchors") if isinstance(existing.get("anchors"), dict) else {},
            "created_at": str(existing.get("created_at") or now),
            "updated_at": now,
        }
    persist_threads(threads, storage=config.storage)
    return build_requirement_management_payload(config)


def load_threads(*, storage: Any | None = None) -> dict[str, dict[str, Any]]:
    if is_sqlite_storage(storage):
        store = store_for(storage)
        with store.connect() as conn:
            return store.load_requirement_threads(conn)
    path = requirement_paths().threads
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in payload.get("requirements") or []:
        if isinstance(item, dict) and item.get("id"):
            result[str(item["id"])] = item
    return result


def persist_threads(threads: dict[str, dict[str, Any]], *, storage: Any | None = None) -> None:
    if is_sqlite_storage(storage):
        store = store_for(storage)
        with store.connect() as conn:
            for requirement_id, thread in threads.items():
                store.save_requirement_thread(conn, requirement_id, thread)
        return
    paths = requirement_paths()
    paths.root.mkdir(parents=True, exist_ok=True)
    threads_payload = {"updated_at": now_iso(), "requirements": sorted(threads.values(), key=lambda item: item.get("updated_at", ""), reverse=True)}
    paths.threads.write_text(json.dumps(threads_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_daily_review(day: date, *, storage: Any | None = None) -> dict[str, Any]:
    if is_sqlite_storage(storage):
        store = store_for(storage)
        with store.connect() as conn:
            return store.load_daily_review(conn, day)
    path = daily_review_path(day)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_daily_review_payload(day: date, payload: dict[str, Any], *, storage: Any | None = None) -> None:
    if is_sqlite_storage(storage):
        store = store_for(storage)
        with store.connect() as conn:
            store.save_daily_review(conn, day, payload)
        return
    paths = requirement_paths()
    paths.daily_dir.mkdir(parents=True, exist_ok=True)
    daily_review_path(day).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def upsert_thread(threads: dict[str, dict[str, Any]], *, requirement_id: str, decision: dict[str, Any]) -> None:
    now = now_iso()
    existing = threads.get(requirement_id, {})
    anchors = merge_anchors(existing.get("anchors"), decision.get("anchors"))
    threads[requirement_id] = {
        "id": requirement_id,
        "title": str(decision.get("title") or existing.get("title") or "").strip(),
        "project": str(decision.get("project") or existing.get("project") or "unknown"),
        "type": str(decision.get("requirement_type") or existing.get("type") or "direct"),
        "status": normalize_thread_status(decision.get("thread_status") or existing.get("status")),
        "note": str(decision.get("note") or existing.get("note") or "").strip(),
        "anchors": anchors,
        "created_at": str(existing.get("created_at") or now),
        "updated_at": now,
    }


def normalize_thread_status(value: Any) -> str:
    status = str(value or "in_progress").strip()
    aliases = {
        "active": "in_progress",
        "open": "in_progress",
        "done": "completed",
        "closed": "completed",
        "complete": "completed",
    }
    status = aliases.get(status, status)
    if status in {"in_progress", "paused", "completed", "archived"}:
        return status
    return "in_progress"


def merge_anchors(left: Any, right: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for source in (left if isinstance(left, dict) else {}, right if isinstance(right, dict) else {}):
        for key, values in source.items():
            if not isinstance(values, list):
                continue
            existing = result.setdefault(str(key), [])
            for value in values:
                text = str(value)
                if text and text not in existing:
                    existing.append(text)
    return result


def requirement_id_for(*, project: str, title: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in f"{project}-{title}")
    slug = "-".join(part for part in slug.split("-") if part)
    if slug:
        return "req_" + slug[:80]
    digest = hashlib.sha1(f"{project}|{title}".encode("utf-8")).hexdigest()[:12]
    return f"req_{digest}"


def apply_requirement_assignments(config: AppConfig, day: date, tasks: list[TaskSummary]) -> None:
    daily_payload = load_daily_review(day, storage=config.storage)
    threads = load_threads(storage=config.storage)
    assignments = [item for item in daily_payload.get("assignments") or [] if isinstance(item, dict)]
    for task in tasks:
        task_event_ids = set(task.event_ids)
        matched = None
        for assignment in assignments:
            if assignment.get("status") != "confirmed":
                continue
            if task_event_ids.intersection(set(str(value) for value in assignment.get("event_ids") or [])):
                matched = assignment
                break
        if not matched:
            continue
        thread = threads.get(str(matched.get("requirement_id") or ""))
        title = str((thread or {}).get("title") or matched.get("title") or "").strip()
        if title:
            task.ai_title = title
        requirement_id = str(matched.get("requirement_id") or "").strip()
        if requirement_id:
            task.requirement_id = requirement_id
        if thread:
            task.requirement_created_at = str(thread.get("created_at") or "").strip() or None
            task.requirement_updated_at = str(thread.get("updated_at") or "").strip() or None


def merge_confirmed_requirement_tasks(tasks: list[TaskSummary]) -> list[TaskSummary]:
    grouped: dict[str, list[TaskSummary]] = {}
    result: list[TaskSummary] = []
    for task in tasks:
        key = confirmed_requirement_group_key(task)
        if key:
            grouped.setdefault(key, []).append(task)
        else:
            result.append(task)

    merged_tasks: list[TaskSummary] = []
    for group_key, requirement_tasks in grouped.items():
        if len(requirement_tasks) == 1:
            merged_tasks.append(requirement_tasks[0])
            continue
        events = [event for task in requirement_tasks for event in task.events]
        title = first_confirmed_title(requirement_tasks)
        merged = task_from_events(events, key=group_key, title=title)
        merged.requirement_id = first_text(task.requirement_id for task in requirement_tasks)
        merged.requirement_created_at = earliest_text(task.requirement_created_at for task in requirement_tasks)
        merged.requirement_updated_at = first_text_reversed(task.requirement_updated_at for task in requirement_tasks)
        merge_task_ai_fields(merged, requirement_tasks)
        merged_tasks.append(merged)

    all_tasks = result + merged_tasks
    all_tasks.sort(key=lambda task: min((event.occurred_at for event in task.events), default=datetime.max.replace(tzinfo=timezone.utc)))
    return all_tasks


def confirmed_requirement_group_key(task: TaskSummary) -> str:
    title = str(task.ai_title or "").strip()
    if title:
        return f"title:{title}"
    if task.requirement_id:
        return f"id:{task.requirement_id}"
    return ""


def first_confirmed_title(tasks: list[TaskSummary]) -> str:
    for task in tasks:
        if task.ai_title:
            return task.ai_title
    return tasks[0].title if tasks else "未命名任务"


def merge_task_ai_fields(target: TaskSummary, tasks: list[TaskSummary]) -> None:
    target.ai_title = first_confirmed_title(tasks)
    target.ai_request = first_text(task.ai_request for task in tasks)
    target.ai_decision = first_text_reversed(task.ai_decision for task in tasks)
    target.ai_outputs = merge_text_lists(task.ai_outputs for task in tasks)
    target.ai_deliverables = merge_text_lists(task.ai_deliverables for task in tasks)
    target.ai_impact = first_text_reversed(task.ai_impact for task in tasks)
    target.ai_evidence = merge_text_lists(task.ai_evidence for task in tasks)
    target.ai_artifact_paths = merge_text_lists(task.ai_artifact_paths for task in tasks)
    target.ai_next = first_text_reversed(task.ai_next for task in tasks)
    target.ai_next_actions = merge_text_lists(task.ai_next_actions for task in tasks)
    target.ai_blockers = merge_text_lists(task.ai_blockers for task in tasks)
    target.ai_questions = merge_text_lists(task.ai_questions for task in tasks)
    target.ai_validation_gaps = merge_text_lists(task.ai_validation_gaps for task in tasks)
    target.ai_owner_hint = first_text_reversed(task.ai_owner_hint for task in tasks)


def merge_text_lists(groups: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
    return result


def first_text(values: Any) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def first_text_reversed(values: Any) -> str | None:
    collected = [str(value or "").strip() for value in values]
    for text in reversed(collected):
        if text:
            return text
    return None


def earliest_text(values: Any) -> str | None:
    collected = sorted(str(value or "").strip() for value in values if str(value or "").strip())
    return collected[0] if collected else None


def write_status(*, day: date, pending_count: int, daily_path: Path, storage: Any | None = None) -> None:
    payload = {
        "date": day.isoformat(),
        "pending_requirements": pending_count,
        "daily_path": str(daily_path),
        "updated_at": now_iso(),
    }
    if is_sqlite_storage(storage):
        store = store_for(storage)
        with store.connect() as conn:
            store.save_status(conn, payload)
        return
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_status(*, storage: Any | None = None) -> dict[str, Any]:
    if is_sqlite_storage(storage):
        store = store_for(storage)
        with store.connect() as conn:
            cleanup_stale_task_runs(conn, store)
            payload = store.load_status(conn)
            day_value = str(payload.get("date") or "").strip()
            status_day = None
            if day_value:
                try:
                    status_day = date.fromisoformat(day_value)
                except ValueError:
                    status_day = None
            payload["active_ai_task_run"] = store.active_ai_task_run(conn, day=status_day) or store.active_ai_task_run(conn)
            payload["latest_ai_task_run"] = store.latest_ai_task_run(conn, day=status_day) or store.latest_ai_task_run(conn)
            return payload
    path = status_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def daily_note_path(config: AppConfig, day: date) -> Path:
    base = config.obsidian.vault_path or config.storage.output_dir
    return base / config.obsidian.daily_dir / f"{day.isoformat()}.md"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()
