from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig
from .ai_cache import (
    ai_result_from_task,
    apply_cached_result,
    context_hash,
    delta_context,
    find_cache_match,
    load_cache,
    prune_cache,
    save_cache,
    task_cache_entry,
)
from .events import WorkEvent
from .merge import TaskSummary, repo_identity, task_from_events, task_key_for_events
from .writers.obsidian import compact_items, is_noise_item, relative_files, unique


@dataclass(frozen=True)
class AiSummaryResult:
    enabled: bool
    used: bool
    message: str


@dataclass(frozen=True)
class ClusterReviewResult:
    enabled: bool
    used: bool
    tasks: list[TaskSummary]
    message: str


def summarize_tasks(config: AppConfig, tasks: list[TaskSummary]) -> AiSummaryResult:
    if not config.ai.enabled:
        return AiSummaryResult(enabled=False, used=False, message="AI summary disabled")
    if config.ai.provider != "deepseek":
        return AiSummaryResult(enabled=True, used=False, message=f"Unsupported AI provider: {config.ai.provider}")
    api_key = os.environ.get(config.ai.api_key_env)
    if not api_key:
        return AiSummaryResult(enabled=True, used=False, message=f"Missing API key env: {config.ai.api_key_env}")
    if not tasks:
        return AiSummaryResult(enabled=True, used=False, message="No tasks to summarize")

    try:
        if config.ai.cache_enabled:
            summarized = summarize_tasks_with_cache(config, api_key, tasks)
        else:
            payload = call_deepseek(config, api_key, tasks)
            apply_ai_payload(tasks, payload)
            summarized = len(tasks)
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as exc:
        return AiSummaryResult(enabled=True, used=False, message=f"AI summary failed: {exc}")
    if config.ai.cache_enabled and summarized == 0:
        return AiSummaryResult(enabled=True, used=True, message="AI summary reused from cache")
    if config.ai.cache_enabled:
        return AiSummaryResult(enabled=True, used=True, message=f"AI summary applied ({summarized} task(s) refreshed)")
    return AiSummaryResult(enabled=True, used=True, message="AI summary applied")


def review_task_clusters(config: AppConfig, tasks: list[TaskSummary]) -> ClusterReviewResult:
    if not config.ai.enabled or not config.ai.cluster_review_enabled:
        return ClusterReviewResult(enabled=False, used=False, tasks=tasks, message="AI cluster review disabled")
    if config.ai.provider != "deepseek":
        return ClusterReviewResult(enabled=True, used=False, tasks=tasks, message=f"Unsupported AI provider: {config.ai.provider}")
    api_key = os.environ.get(config.ai.api_key_env)
    if not api_key:
        return ClusterReviewResult(enabled=True, used=False, tasks=tasks, message=f"Missing API key env: {config.ai.api_key_env}")
    if not needs_cluster_review(tasks):
        return ClusterReviewResult(enabled=True, used=False, tasks=tasks, message="AI cluster review skipped")

    try:
        review_context = cluster_review_context(tasks)
        input_hash = context_hash({"tasks": review_context})
        cached_payload = load_cluster_review_cache(config, tasks, input_hash)
        if cached_payload is not None:
            reviewed_tasks = apply_cluster_review_payload(tasks, cached_payload, min_confidence=config.ai.cluster_review_min_confidence)
            if same_task_groups(tasks, reviewed_tasks):
                return ClusterReviewResult(enabled=True, used=True, tasks=tasks, message="AI cluster review reused cached original groups")
            return ClusterReviewResult(
                enabled=True,
                used=True,
                tasks=reviewed_tasks,
                message=f"AI cluster review reused cached adjustment ({len(reviewed_tasks)} task group(s))",
            )
        payload = call_deepseek_for_prompt(config, api_key, build_cluster_review_prompt_from_context(review_context))
        save_cluster_review_cache(config, tasks, input_hash, payload)
        reviewed_tasks = apply_cluster_review_payload(tasks, payload, min_confidence=config.ai.cluster_review_min_confidence)
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as exc:
        return ClusterReviewResult(enabled=True, used=False, tasks=tasks, message=f"AI cluster review failed: {exc}")
    if same_task_groups(tasks, reviewed_tasks):
        return ClusterReviewResult(enabled=True, used=True, tasks=tasks, message="AI cluster review kept original groups")
    return ClusterReviewResult(enabled=True, used=True, tasks=reviewed_tasks, message=f"AI cluster review adjusted {len(reviewed_tasks)} task group(s)")


def needs_cluster_review(tasks: list[TaskSummary]) -> bool:
    meaningful = [task for task in tasks if task.event_count > 0]
    if not meaningful:
        return False

    tasks_by_repo: dict[str, int] = {}
    for task in meaningful:
        repo = repo_identity(task.cwd)
        tasks_by_repo[repo] = tasks_by_repo.get(repo, 0) + 1
    if any(count > 1 for count in tasks_by_repo.values()):
        return True

    return any(task.event_count > 1 and len(task.session_ids) > 1 for task in meaningful)


def summarize_tasks_with_cache(config: AppConfig, api_key: str, tasks: list[TaskSummary]) -> int:
    day = tasks[0].day
    cache = load_cache(config.ai.cache_dir, day)
    cache_entries = [entry for entry in cache.get("tasks") or [] if isinstance(entry, dict)]
    contexts_by_key: dict[str, dict[str, Any]] = {}
    request_items: list[dict[str, Any]] = []

    for task in tasks:
        context = task_context(task)
        contexts_by_key[task.key] = context
        match = find_cache_match(task, cache_entries)
        current_event_ids = set(task.event_ids)
        if match and len(match.entries) == 1 and current_event_ids == match.event_ids:
            results = match.ai_results
            if results:
                apply_cached_result(task, results[0])
                if cached_ai_result_has_deliverables(results[0]):
                    continue
        if match and not current_event_ids.issubset(match.event_ids):
            request_items.append(
                {
                    "mode": "merge_delta",
                    "key": task.key,
                    "project": context.get("project"),
                    "sources": context.get("sources"),
                    "previous_ai_results": match.ai_results,
                    "delta_events": delta_context(context, match.contexts),
                }
            )
        else:
            request_items.append({"mode": "new_task", "key": task.key, "task": context})

    if request_items:
        payload = call_deepseek_for_prompt(config, api_key, build_incremental_prompt(request_items))
        apply_ai_payload(tasks, payload)

    save_cache(
        config.ai.cache_dir,
        day,
        [task_cache_entry(task, context=contexts_by_key[task.key], ai_result=ai_result_from_task(task)) for task in tasks],
    )
    prune_cache(config.ai.cache_dir, keep_days=config.ai.cache_retention_days, today=day)
    return len(request_items)


def cached_ai_result_has_deliverables(result: dict[str, Any]) -> bool:
    return all(key in result for key in ("deliverables", "impact", "evidence", "artifact_paths"))


def call_deepseek(config: AppConfig, api_key: str, tasks: list[TaskSummary]) -> Any:
    return call_deepseek_for_prompt(config, api_key, build_prompt(tasks))


def call_deepseek_for_prompt(config: AppConfig, api_key: str, prompt: str) -> Any:
    body = {
        "model": config.ai.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个严谨的工作日志整理助手。只根据输入摘要整理，不编造。"
                    "输出必须是 JSON 数组，不要输出 Markdown。"
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "stream": False,
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        url=config.ai.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config.ai.timeout_seconds) as response:
        data = json.loads(response.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return parse_json_content(content)


def build_prompt(tasks: list[TaskSummary]) -> str:
    compact_tasks = []
    for task in tasks:
        compact_tasks.append(task_context(task))
    return (
        "请把以下工作事件整理成适合 Obsidian Daily 的任务摘要。\n"
        "要求：\n"
        "1. 每个任务输出一个对象，key 必须原样保留。\n"
        "2. title 使用 8-24 个中文字符，像人写的任务标题。\n"
        "3. request 保留原始需求核心，不超过 100 字。\n"
        "4. decision 写最终结论或当前进展，不超过 140 字；没有就写“暂无明确结论”。\n"
        "5. deliverables 最多 3 条，识别真正完成的功能、修复、文档、测试、发布或排障结论，不要只罗列文件名。\n"
        "6. impact 写一句话说明对用户、项目或后续工作的影响；没有就写“暂无明确影响”。\n"
        "7. evidence 最多 3 条，写可验证证据，例如测试通过、真实执行结果、commit/tag、生成文件，不要编造。\n"
        "8. artifact_paths 最多 5 条，只放重要相对路径或短路径，不要堆完整绝对路径。\n"
        "9. outputs 与 deliverables 保持一致，用于兼容旧版本展示。\n"
        "10. next 写一句话后续摘要，没有就写“待确认”。\n"
        "11. 输入已经压缩，但可能仍包含阶段性过程；请优先使用 original_request 和 latest_decisions，不要被中间过程带偏。\n"
        "12. 过滤测试、寒暄、纯确认词和工具噪音。\n"
        "13. next_actions 写明确可执行的下一步，最多 5 条；没有输出空数组。\n"
        "14. blockers 写当前阻塞，最多 5 条；没有输出空数组。\n"
        "15. questions 写需要用户或外部确认的问题，最多 5 条；没有输出空数组。\n"
        "16. validation_gaps 写未完成的测试、验证、观察项，最多 5 条；没有输出空数组。\n"
        "17. owner_hint 只能是 user、agent、external、none 之一，用来表示后续主要责任方。\n"
        "JSON 字段：key,title,request,decision,outputs,deliverables,impact,evidence,artifact_paths,next,next_actions,blockers,questions,validation_gaps,owner_hint。\n\n"
        + json.dumps(compact_tasks, ensure_ascii=False)
    )


def build_incremental_prompt(items: list[dict[str, Any]]) -> str:
    return (
        "请基于以下增量工作事件更新适合 Obsidian Daily 的任务摘要。\n"
        "要求：\n"
        "1. 每个输入对象都必须输出一个对象，key 必须原样保留。\n"
        "2. mode=new_task 时，根据 task 生成完整摘要。\n"
        "3. mode=merge_delta 时，不能假设已有完整上下文；只能基于 previous_ai_results 和 delta_events 输出合并后的最新任务状态。\n"
        "4. merge_delta 不要丢失仍然有效的旧待办；如果新增事件说明旧阻塞、待确认或验证缺口已解决，则移除它。\n"
        "5. request 保留最初核心需求，必要时补充新增需求；decision 以最新结论为准但保留关键历史结论。\n"
        "6. deliverables 最多 3 条，识别真正完成的功能、修复、文档、测试、发布或排障结论；outputs 与 deliverables 保持一致。\n"
        "7. impact 写一句话影响；evidence 最多 3 条；artifact_paths 最多 5 条，只放重要相对路径或短路径。\n"
        "8. next_actions/blockers/questions/validation_gaps 各最多 5 条。\n"
        "9. owner_hint 只能是 user、agent、external、none 之一。\n"
        "10. 只根据输入摘要整理，不编造。\n"
        "JSON 字段：key,title,request,decision,outputs,deliverables,impact,evidence,artifact_paths,next,next_actions,blockers,questions,validation_gaps,owner_hint。\n\n"
        + json.dumps(items, ensure_ascii=False)
    )


def build_cluster_review_prompt(tasks: list[TaskSummary]) -> str:
    return build_cluster_review_prompt_from_context(cluster_review_context(tasks))


def build_cluster_review_prompt_from_context(context: list[dict[str, Any]]) -> str:
    return (
        "请审查以下由规则初步聚类得到的工作任务，判断是否需要合并同一任务或拆分误合并任务。\n"
        "要求：\n"
        "1. 只根据输入里的压缩事件摘要判断，不编造，不要求额外上下文。\n"
        "2. 输出必须是 JSON 对象，字段为 groups。\n"
        "3. groups 是数组，每个对象包含 title,event_ids,confidence,reason。\n"
        "4. event_ids 只能使用输入中出现的 id；同一个 event_id 只能出现在一个高置信度 group 中。\n"
        "5. confidence 范围 0-1。只有你非常确定同属一个任务或必须拆分时，confidence 才应 >= 0.75。\n"
        "6. 低置信度时保留原聚类：输出原任务对应的 event_ids，confidence 低于 0.75，并说明原因。\n"
        "7. 不要输出 Markdown，不要输出解释性正文。\n\n"
        + json.dumps(context, ensure_ascii=False)
    )


def cluster_review_cache_path(config: AppConfig, tasks: list[TaskSummary]) -> Path:
    day = tasks[0].day
    return config.ai.cache_dir / "cluster-review" / f"{day.isoformat()}.json"


def load_cluster_review_cache(config: AppConfig, tasks: list[TaskSummary], input_hash: str) -> Any | None:
    if not config.ai.cache_enabled:
        return None
    path = cluster_review_cache_path(config, tasks)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("input_hash") != input_hash:
        return None
    return payload.get("review")


def save_cluster_review_cache(config: AppConfig, tasks: list[TaskSummary], input_hash: str, review: Any) -> None:
    if not config.ai.cache_enabled:
        return
    path = cluster_review_cache_path(config, tasks)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": tasks[0].day.isoformat(),
        "input_hash": input_hash,
        "review": review,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    prune_cache(path.parent, keep_days=config.ai.cache_retention_days, today=tasks[0].day)


def cluster_review_context(tasks: list[TaskSummary]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for task in tasks:
        result.append(
            {
                "key": task.key,
                "title": task.title,
                "project": repo_identity(task.cwd),
                "sources": sorted(task.sources),
                "event_ids": sorted(task.event_ids),
                "session_ids": sorted(task.session_ids),
                "files": compact_items(relative_files(task), limit=8, char_limit=140),
                "events": [event_review_context(event) for event in task.events[:8]],
            }
        )
    return result


def event_review_context(event: WorkEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "source": event.source,
        "type": event.event_type,
        "summary": truncate_text(event.summary, 220),
        "raw_request": truncate_text(event.raw_request or "", 260),
        "decision": truncate_text(event.decision or "", 220),
        "files": [truncate_text(Path(file).name, 80) for file in event.files[:6]],
        "session_id": str(event.metadata.get("session_id") or ""),
    }


def apply_cluster_review_payload(tasks: list[TaskSummary], payload: Any, *, min_confidence: float) -> list[TaskSummary]:
    if not isinstance(payload, dict):
        raise ValueError("AI cluster review response must be a JSON object")
    groups = payload.get("groups")
    if not isinstance(groups, list):
        raise ValueError("AI cluster review response must contain groups")

    event_by_id: dict[str, WorkEvent] = {}
    original_key_by_event_set = {frozenset(task.event_ids): task.key for task in tasks}
    for task in tasks:
        for event in task.events:
            event_by_id[event.id] = event

    consumed: set[str] = set()
    reviewed: list[TaskSummary] = []
    for item in groups:
        if not isinstance(item, dict):
            continue
        confidence = numeric_confidence(item.get("confidence"))
        if confidence < min_confidence:
            continue
        event_ids = clean_event_ids(item.get("event_ids"))
        if not event_ids or any(event_id not in event_by_id or event_id in consumed for event_id in event_ids):
            continue
        title = clean_text(item.get("title"))
        key = original_key_by_event_set.get(frozenset(event_ids))
        if not key:
            key = task_key_for_events([event_by_id[event_id] for event_id in event_ids])
        reviewed.append(task_from_events([event_by_id[event_id] for event_id in event_ids], key=key, title=title))
        consumed.update(event_ids)

    for task in tasks:
        remaining_events = [event for event in task.events if event.id not in consumed]
        if remaining_events:
            reviewed.append(task_from_events(remaining_events, key=task.key, title=task.title))

    reviewed.sort(key=lambda task: min((event.occurred_at for event in task.events), default=None))
    return reviewed


def clean_event_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def numeric_confidence(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def same_task_groups(left: list[TaskSummary], right: list[TaskSummary]) -> bool:
    return {frozenset(task.event_ids) for task in left} == {frozenset(task.event_ids) for task in right}


def task_context(task: TaskSummary) -> dict[str, Any]:
    return {
        "key": task.key,
        "title": task.title,
        "project": repo_identity(task.cwd),
        "sources": sorted(task.sources),
        "event_count": task.event_count,
        "original_request": first_meaningful(task.raw_requests, char_limit=900),
        "additional_requests": select_meaningful(task.raw_requests[1:], limit=3, char_limit=320),
        "latest_decisions": select_latest_meaningful(task.decisions, limit=4, char_limit=520),
        "process_evidence": select_meaningful(task.discussions, limit=3, char_limit=180),
        "files": compact_items(relative_files(task), limit=10, char_limit=160),
    }


def first_meaningful(items: list[str], *, char_limit: int) -> str:
    selected = select_meaningful(items, limit=1, char_limit=char_limit)
    return selected[0] if selected else ""


def select_meaningful(items: list[str], *, limit: int, char_limit: int) -> list[str]:
    selected: list[str] = []
    for item in unique(items):
        if is_noise_item(item) or is_low_value_text(item):
            continue
        selected.append(truncate_text(item, char_limit))
        if len(selected) >= limit:
            break
    return selected


def select_latest_meaningful(items: list[str], *, limit: int, char_limit: int) -> list[str]:
    selected: list[str] = []
    for item in reversed(unique(items)):
        if is_noise_item(item) or is_low_value_text(item):
            continue
        selected.append(truncate_text(item, char_limit))
        if len(selected) >= limit:
            break
    return list(reversed(selected))


def truncate_text(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def is_low_value_text(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {"y", "yes", "ok", "okay", "好", "好的", "收到", "test", "tes", "记录一下"}


def parse_json_content(content: str) -> Any:
    stripped = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.S)
    if fence:
        stripped = fence.group(1).strip()
    return json.loads(stripped)


def apply_ai_payload(tasks: list[TaskSummary], payload: Any) -> None:
    if not isinstance(payload, list):
        raise ValueError("AI response must be a JSON array")
    by_key = {task.key: task for task in tasks}
    for item in payload:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        task = by_key.get(key)
        if not task:
            continue
        task.ai_title = clean_text(item.get("title"))
        task.ai_request = clean_text(item.get("request"))
        task.ai_decision = clean_text(item.get("decision"))
        outputs = item.get("outputs")
        if isinstance(outputs, list):
            task.ai_outputs = clean_text_list(outputs, limit=3, char_limit=140)
        task.ai_deliverables = clean_text_list(item.get("deliverables"), limit=3, char_limit=140)
        if not task.ai_deliverables and task.ai_outputs:
            task.ai_deliverables = list(task.ai_outputs)
        task.ai_impact = clean_text(item.get("impact"))
        task.ai_evidence = clean_text_list(item.get("evidence"), limit=3, char_limit=140)
        task.ai_artifact_paths = clean_text_list(item.get("artifact_paths"), limit=5, char_limit=140)
        task.ai_next = clean_text(item.get("next"))
        task.ai_next_actions = clean_text_list(item.get("next_actions"), limit=5, char_limit=120)
        task.ai_blockers = clean_text_list(item.get("blockers"), limit=5, char_limit=120)
        task.ai_questions = clean_text_list(item.get("questions"), limit=5, char_limit=120)
        task.ai_validation_gaps = clean_text_list(item.get("validation_gaps"), limit=5, char_limit=120)
        task.ai_owner_hint = clean_owner_hint(item.get("owner_hint"))


def clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def clean_text_list(value: object, *, limit: int, char_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        cleaned = clean_text(item)
        if not cleaned or is_low_value_text(cleaned):
            continue
        cleaned = truncate_text(cleaned, char_limit)
        if cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def clean_owner_hint(value: object) -> str | None:
    cleaned = clean_text(value)
    if not cleaned:
        return None
    normalized = cleaned.lower()
    return normalized if normalized in {"user", "agent", "external", "none"} else None
