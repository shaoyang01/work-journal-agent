from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .ai import review_task_clusters
from .config import AppConfig, default_data_dir
from .events import WorkEvent, read_events
from .merge import TaskSummary, group_events, repo_identity
from .writers.obsidian import compact_items, display_title, relative_files


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
    events = [event for event in read_events(config.storage.inbox_path) if event.occurred_at.date() == day]
    tasks = group_events(events, min_keyword_overlap=config.merge.min_keyword_overlap)
    cluster_result = review_task_clusters(config, tasks)
    tasks = cluster_result.tasks
    existing_daily = load_daily_review(day)
    candidates = [candidate_from_task(task, existing_daily=existing_daily) for task in tasks if task.event_count > 0]
    ignored_event_ids = set(existing_daily.get("ignored_event_ids") or [])
    pending = [candidate for candidate in candidates if candidate.get("status") not in {"confirmed", "ignored"}]
    payload = {
        "date": day.isoformat(),
        "generated_at": now_iso(),
        "candidates": candidates,
        "ignored_event_ids": sorted(ignored_event_ids),
        "summary": {
            "total_candidates": len(candidates),
            "pending_candidates": len(pending),
            "event_count": sum(task.event_count for task in tasks),
            "cluster_review": cluster_result.message,
        },
    }
    write_status(day=day, pending_count=len(pending), daily_path=daily_note_path(config, day))
    return payload


def candidate_from_task(task: TaskSummary, *, existing_daily: dict[str, Any]) -> dict[str, Any]:
    candidate_id = candidate_id_for_task(task)
    existing = assignment_for_candidate(existing_daily, candidate_id)
    title = str(existing.get("title") or display_title(task))
    project = repo_identity(task.cwd) or "unknown"
    anchors = anchors_for_task(task)
    confidence = confidence_for_task(task, title=title, anchors=anchors)
    status = str(existing.get("status") or ("pending" if confidence < 0.85 else "suggested"))
    return {
        "candidate_id": candidate_id,
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
    paths = requirement_paths()
    paths.daily_dir.mkdir(parents=True, exist_ok=True)
    paths.root.mkdir(parents=True, exist_ok=True)
    threads = load_threads()
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
        title = str(decision.get("title") or "").strip()
        project = str(decision.get("project") or "unknown").strip() or "unknown"
        requirement_id = requirement_id_for(project=project, title=title)
        upsert_thread(threads, requirement_id=requirement_id, decision=decision)
        saved_assignments.append(normalize_assignment(decision, requirement_id=requirement_id))
    threads_payload = {"updated_at": now_iso(), "requirements": sorted(threads.values(), key=lambda item: item["updated_at"], reverse=True)}
    paths.threads.write_text(json.dumps(threads_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    daily_payload = {
        "date": day.isoformat(),
        "updated_at": now_iso(),
        "assignments": saved_assignments,
        "ignored_event_ids": sorted(set(ignored_event_ids)),
    }
    daily_review_path(day).write_text(json.dumps(daily_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending_count = len([item for item in saved_assignments if item.get("status") not in {"confirmed", "ignored"}])
    write_status(day=day, pending_count=pending_count, daily_path=daily_note_path(config, day))
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


def load_threads() -> dict[str, dict[str, Any]]:
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


def load_daily_review(day: date) -> dict[str, Any]:
    path = daily_review_path(day)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def upsert_thread(threads: dict[str, dict[str, Any]], *, requirement_id: str, decision: dict[str, Any]) -> None:
    now = now_iso()
    existing = threads.get(requirement_id, {})
    anchors = merge_anchors(existing.get("anchors"), decision.get("anchors"))
    threads[requirement_id] = {
        "id": requirement_id,
        "title": str(decision.get("title") or existing.get("title") or "").strip(),
        "project": str(decision.get("project") or existing.get("project") or "unknown"),
        "type": str(decision.get("requirement_type") or existing.get("type") or "direct"),
        "status": str(decision.get("thread_status") or existing.get("status") or "in_progress"),
        "anchors": anchors,
        "created_at": str(existing.get("created_at") or now),
        "updated_at": now,
    }


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
    daily_payload = load_daily_review(day)
    threads = load_threads()
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


def write_status(*, day: date, pending_count: int, daily_path: Path) -> None:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": day.isoformat(),
        "pending_requirements": pending_count,
        "daily_path": str(daily_path),
        "updated_at": now_iso(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def daily_note_path(config: AppConfig, day: date) -> Path:
    base = config.obsidian.vault_path or config.storage.output_dir
    return base / config.obsidian.daily_dir / f"{day.isoformat()}.md"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()
