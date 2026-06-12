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
    delta_context,
    find_cache_match,
    load_cache,
    prune_cache,
    save_cache,
    task_cache_entry,
)
from .merge import TaskSummary, repo_identity
from .writers.obsidian import compact_items, is_noise_item, relative_files, unique


@dataclass(frozen=True)
class AiSummaryResult:
    enabled: bool
    used: bool
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
        if match and len(match.entries) == 1 and not (set(task.event_ids) - match.event_ids):
            results = match.ai_results
            if results:
                apply_cached_result(task, results[0])
                continue
        if match:
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
        "5. outputs 最多 3 条，写关键产出，不要堆完整绝对路径。\n"
        "6. next 写一句话后续摘要，没有就写“待确认”。\n"
        "7. 输入已经压缩，但可能仍包含阶段性过程；请优先使用 original_request 和 latest_decisions，不要被中间过程带偏。\n"
        "8. 过滤测试、寒暄、纯确认词和工具噪音。\n"
        "9. next_actions 写明确可执行的下一步，最多 5 条；没有输出空数组。\n"
        "10. blockers 写当前阻塞，最多 5 条；没有输出空数组。\n"
        "11. questions 写需要用户或外部确认的问题，最多 5 条；没有输出空数组。\n"
        "12. validation_gaps 写未完成的测试、验证、观察项，最多 5 条；没有输出空数组。\n"
        "13. owner_hint 只能是 user、agent、external、none 之一，用来表示后续主要责任方。\n"
        "JSON 字段：key,title,request,decision,outputs,next,next_actions,blockers,questions,validation_gaps,owner_hint。\n\n"
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
        "6. outputs 最多 3 条，next_actions/blockers/questions/validation_gaps 各最多 5 条。\n"
        "7. owner_hint 只能是 user、agent、external、none 之一。\n"
        "8. 只根据输入摘要整理，不编造。\n"
        "JSON 字段：key,title,request,decision,outputs,next,next_actions,blockers,questions,validation_gaps,owner_hint。\n\n"
        + json.dumps(items, ensure_ascii=False)
    )


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
