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
        payload = call_deepseek(config, api_key, tasks)
        apply_ai_payload(tasks, payload)
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as exc:
        return AiSummaryResult(enabled=True, used=False, message=f"AI summary failed: {exc}")
    return AiSummaryResult(enabled=True, used=True, message="AI summary applied")


def call_deepseek(config: AppConfig, api_key: str, tasks: list[TaskSummary]) -> Any:
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
                "content": build_prompt(tasks),
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
        "6. next 写待确认事项，没有就写“待确认”。\n"
        "7. 输入已经压缩，但可能仍包含阶段性过程；请优先使用 original_request 和 latest_decisions，不要被中间过程带偏。\n"
        "8. 过滤测试、寒暄、纯确认词和工具噪音。\n"
        "JSON 字段：key,title,request,decision,outputs,next。\n\n"
        + json.dumps(compact_tasks, ensure_ascii=False)
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
            task.ai_outputs = [clean_text(value) for value in outputs if clean_text(value)]
        task.ai_next = clean_text(item.get("next"))


def clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None
