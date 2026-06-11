from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from ..config import AppConfig
from ..merge import TaskSummary, repo_identity


def write_daily(config: AppConfig, day: date, tasks: list[TaskSummary]) -> Path:
    tasks = [task for task in tasks if not is_noise_task(task)]
    base = config.obsidian.vault_path or config.storage.output_dir
    daily_dir = base / config.obsidian.daily_dir
    daily_dir.mkdir(parents=True, exist_ok=True)
    target = daily_dir / f"{day.isoformat()}.md"
    target.write_text(render_daily(day, tasks), encoding="utf-8")

    if config.obsidian.write_task_notes:
        task_dir = base / config.obsidian.task_dir
        task_dir.mkdir(parents=True, exist_ok=True)
        for task in tasks:
            task_target = task_dir / f"{day.isoformat()}-{slugify(display_title(task))}.md"
            task_target.write_text(render_task(task), encoding="utf-8")
    return target


def render_daily(day: date, tasks: list[TaskSummary]) -> str:
    tasks = [task for task in tasks if not is_noise_task(task)]
    lines = [
        "---",
        f"date: {day.isoformat()}",
        "type: daily-work-journal",
        "---",
        "",
        f"# {day.isoformat()} 工作记录",
        "",
    ]
    if not tasks:
        lines.extend(["今天没有可归档的工作事件。", ""])
        return "\n".join(lines)

    lines.extend(["## 今日概览", ""])
    for task in tasks:
        lines.append(f"- **{display_title(task)}**（{repo_identity(task.cwd) or 'unknown'}，{', '.join(sorted(task.sources)) or 'unknown'}）")
    lines.extend(["", "## 任务详情", ""])
    for task in tasks:
        lines.extend(render_task_brief(task).splitlines())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_task_brief(task: TaskSummary) -> str:
    sources = ", ".join(sorted(task.sources)) or "unknown"
    project = repo_identity(task.cwd) or "unknown"
    request = task.ai_request or first_compact(task.raw_requests, fallback="未记录明确原始需求。")
    decision = task.ai_decision or compact_decision(task.decisions)
    files = compact_ai_outputs(task) if task.ai_outputs else compact_files(task)
    next_step = task.ai_next or "待确认。"
    lines = [
        f"### {display_title(task)}",
        "",
        f"- 项目：{project}",
        f"- 来源：{sources}",
        f"- 事件：{task.event_count} 条",
        f"- 需求：{request}",
        f"- 结论：{decision}",
        f"- 产出：{files}",
        f"- 后续：{next_step}",
    ]
    return "\n".join(lines)


def render_task(task: TaskSummary) -> str:
    sources = ", ".join(sorted(task.sources)) or "unknown"
    project = repo_identity(task.cwd) or "unknown"
    lines = [
        f"## {display_title(task)}",
        "",
        f"来源：{sources}",
        f"项目：{project}",
        f"事件数：{task.event_count}",
        "",
        "### 原始需求",
    ]
    lines.extend(bullets(compact_items(task.raw_requests, limit=3), fallback="未记录明确原始需求。"))
    lines.extend(["", "### 关键过程"])
    lines.extend(bullets(compact_items(task.discussions, limit=3), fallback="未记录关键过程。"))
    lines.extend(["", "### 最终结论"])
    lines.extend(bullets(compact_items(task.decisions, limit=3), fallback="暂无明确结论。"))
    lines.extend(["", "### 产出"])
    lines.extend(bullets(compact_items(relative_files(task), limit=8, char_limit=160), fallback="暂无文件产出记录。"))
    lines.extend(["", "### 后续", "- 待确认。"])
    return "\n".join(lines)


def bullets(items: list[str], *, fallback: str) -> list[str]:
    if not items:
        return [fallback]
    return [f"- {item}" for item in items]


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = " ".join(item.split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def compact_items(items: list[str], *, limit: int = 5, char_limit: int = 260) -> list[str]:
    compacted: list[str] = []
    for item in unique(items):
        if is_noise_item(item):
            continue
        compacted.append(truncate_item(item, char_limit))
        if len(compacted) >= limit:
            break
    remaining = len([item for item in unique(items) if not is_noise_item(item)]) - len(compacted)
    if remaining > 0:
        compacted.append(f"另有 {remaining} 条已折叠。")
    return compacted


def first_compact(items: list[str], *, fallback: str, char_limit: int = 120) -> str:
    compacted = compact_items(items, limit=1, char_limit=char_limit)
    if not compacted:
        return fallback
    return compacted[0]


def compact_files(task: TaskSummary) -> str:
    files = relative_files(task)
    if not files:
        return "暂无文件产出记录。"
    shown = compact_items(files, limit=3, char_limit=90)
    return "；".join(shown)


def compact_decision(items: list[str]) -> str:
    useful = [item for item in unique(items) if not is_noise_item(item)]
    if not useful:
        return "暂无明确结论。"
    return truncate_item(useful[-1], 120)


def relative_files(task: TaskSummary) -> list[str]:
    cwd = Path(task.cwd).expanduser().resolve() if task.cwd else None
    result: list[str] = []
    for file in sorted(task.files):
        path = Path(file).expanduser()
        if cwd:
            try:
                result.append(str(path.resolve().relative_to(cwd)))
                continue
            except (OSError, ValueError):
                pass
        result.append(str(path))
    return result


def display_title(task: TaskSummary) -> str:
    title = truncate_item(task.ai_title or task.title, 42)
    return title or "未命名任务"


def compact_ai_outputs(task: TaskSummary) -> str:
    shown = compact_items(task.ai_outputs, limit=3, char_limit=90)
    return "；".join(shown) if shown else "暂无文件产出记录。"


def truncate_item(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def is_noise_item(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith(("<skill>", "# Files mentioned by the user:", "<turn_aborted>")) or stripped in {
        "Claude 事件：Stop",
        "Codex 事件：Stop",
    }


def is_noise_task(task: TaskSummary) -> bool:
    title = task.title.strip().lower()
    task_text = " ".join([task.title, *task.raw_requests, *task.discussions, *task.decisions]).lower()
    return task.event_count == 1 and any(marker in task_text for marker in ("测试", "test"))


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value.strip().lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:80] or "task"
