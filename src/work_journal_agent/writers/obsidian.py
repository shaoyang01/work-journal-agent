from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..merge import TaskSummary, repo_identity


def write_daily(
    config: AppConfig,
    day: date,
    tasks: list[TaskSummary],
    *,
    knowledge_topics: list[dict[str, Any]] | None = None,
    write_daily_note: bool = True,
    write_knowledge: bool = True,
) -> Path:
    tasks = [task for task in tasks if not is_noise_task(task)]
    base = config.obsidian.vault_path or config.storage.output_dir
    daily_dir = base / config.obsidian.daily_dir
    daily_dir.mkdir(parents=True, exist_ok=True)
    target = daily_dir / f"{day.isoformat()}.md"
    if write_daily_note:
        target.write_text(render_daily(day, tasks), encoding="utf-8")

    if write_daily_note and config.obsidian.write_task_notes:
        task_dir = base / config.obsidian.task_dir
        task_dir.mkdir(parents=True, exist_ok=True)
        for task in tasks:
            task_target = task_dir / f"{day.isoformat()}-{slugify(display_title(task))}.md"
            task_target.write_text(render_task(task), encoding="utf-8")
    if write_knowledge and config.obsidian.write_knowledge_notes:
        write_knowledge_topic_notes(config, day, tasks, topics=knowledge_topics)
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
    files = compact_deliverables(task)
    next_step = task.ai_next or "待确认。"
    lines = [
        f"### {display_title(task)}",
        "",
        f"- 项目：{project}",
        f"- 来源：{sources}",
        f"- 事件：{task.event_count} 条",
    ]
    duration = requirement_duration_label(task, day=task.day)
    if duration:
        lines.append(f"- 已进行：{duration}")
    lines.extend(
        [
            f"- 需求：{request}",
            f"- 结论：{decision}",
            f"- 产出：{files}",
            f"- 后续：{next_step}",
        ]
    )
    if task.ai_impact and task.ai_impact != "暂无明确影响":
        lines.append(f"- 影响：{task.ai_impact}")
    if task.ai_evidence:
        lines.append(f"- 证据：{compact_followup_items(task.ai_evidence)}")
    if task.ai_artifact_paths:
        lines.append(f"- 产物路径：{compact_followup_items(task.ai_artifact_paths)}")
    lines.extend(render_followup_lines(task))
    return "\n".join(lines)


def requirement_duration_label(task: TaskSummary, *, day: date) -> str:
    if not task.requirement_created_at:
        return ""
    created_at = parse_datetime(task.requirement_created_at)
    if not created_at:
        return ""
    if created_at.date() == day:
        return "今天开始"
    elapsed_days = max(1, (day - created_at.date()).days)
    return f"{elapsed_days} 天"


def parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


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
    lines.extend(["", "### 重要产出"])
    lines.extend(bullets(compact_items(task.ai_deliverables or task.ai_outputs, limit=5, char_limit=160), fallback=compact_files(task)))
    if task.ai_impact and task.ai_impact != "暂无明确影响":
        lines.extend(["", "### 影响"])
        lines.append(task.ai_impact)
    if task.ai_evidence:
        lines.extend(["", "### 证据"])
        lines.extend(bullets(compact_items(task.ai_evidence, limit=5, char_limit=160), fallback="暂无证据记录。"))
    if task.ai_artifact_paths:
        lines.extend(["", "### 产物路径"])
        lines.extend(bullets(compact_items(task.ai_artifact_paths, limit=8, char_limit=160), fallback="暂无产物路径记录。"))
    lines.extend(["", "### 后续"])
    followups = render_task_followup_bullets(task)
    lines.extend(followups if followups else ["- 待确认。"])
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


def compact_deliverables(task: TaskSummary) -> str:
    shown = compact_items(task.ai_deliverables or task.ai_outputs, limit=3, char_limit=90)
    return "；".join(shown) if shown else compact_files(task)


def write_knowledge_topic_notes(config: AppConfig, day: date, tasks: list[TaskSummary], *, topics: list[dict[str, Any]] | None = None) -> list[Path]:
    base = config.obsidian.vault_path or config.storage.output_dir
    knowledge_dir = base / config.obsidian.knowledge_dir
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    if topics is not None:
        return write_ai_knowledge_topics(knowledge_dir, day=day, daily_dir=config.obsidian.daily_dir, topics=topics)
    return []


def write_ai_knowledge_topics(knowledge_dir: Path, *, day: date, daily_dir: str, topics: list[dict[str, Any]]) -> list[Path]:
    written: list[Path] = []
    for topic in topics:
        title = clean_topic_text(topic.get("title")) or clean_topic_text(topic.get("topic"))
        service = clean_topic_text(topic.get("service")) or clean_topic_text(topic.get("project")) or clean_topic_text(topic.get("codebase"))
        if not title or not service:
            continue
        service_dir = knowledge_dir / slugify(service)
        service_dir.mkdir(parents=True, exist_ok=True)
        target = service_dir / f"{slugify(title)}.md"
        upsert_ai_knowledge_entry(target, service=service, topic=title, day=day, daily_dir=daily_dir, payload=topic)
        written.append(target)
    return written


def upsert_ai_knowledge_entry(target: Path, *, service: str, topic: str, day: date, daily_dir: str, payload: dict[str, Any]) -> None:
    entry = render_ai_knowledge_reference_entry(day, daily_dir, payload)
    references = {}
    if target.exists():
        content = target.read_text(encoding="utf-8")
        references = parse_reference_entries(content)
    else:
        content = render_ai_knowledge_shell(service, payload)
    references[day.isoformat()] = entry
    target.write_text(render_ai_knowledge_document(service, topic, payload, references).rstrip() + "\n", encoding="utf-8")


def render_ai_knowledge_document(service: str, topic: str, payload: dict[str, Any], references: dict[str, str]) -> str:
    lines = [
        render_ai_knowledge_shell(service, payload).rstrip(),
        "",
        render_ai_knowledge_current_section(topic, payload).rstrip(),
        "",
        "### 参考索引",
        "",
    ]
    for key in sorted(references):
        lines.append(references[key].rstrip())
        lines.append("")
    return "\n".join(lines).rstrip()


def render_ai_knowledge_shell(service: str, payload: dict[str, Any]) -> str:
    topic = clean_topic_text(payload.get("title")) or clean_topic_text(payload.get("topic")) or service
    tags = clean_topic_list(payload.get("tags"), limit=8)
    lines = [
        "---",
        "type: knowledge-topic",
        f'service: "{service}"',
        f'topic: "{topic}"',
    ]
    if tags:
        lines.append("tags:")
        lines.extend([f"  - {tag}" for tag in tags])
    lines.extend(["---", "", f"# {topic}", ""])
    return "\n".join(lines)


def normalize_knowledge_headings(content: str) -> str:
    return content.replace("\n## 时间线\n", "\n## 参考索引\n")


def render_ai_knowledge_current_section(topic: str, payload: dict[str, Any]) -> str:
    lines = [
        "### 专题定位",
        "",
    ]
    problem_space = clean_topic_text(payload.get("problem_space")) or clean_topic_text(payload.get("summary"))
    lines.append(problem_space or "暂无明确专题边界。")
    append_section(lines, render_named_bullets("代码位置", clean_topic_list(payload.get("code_locations"), limit=8), ""))
    core_logic = remove_duplicate_text(clean_topic_list(payload.get("core_logic") or payload.get("durable_insights") or payload.get("key_points"), limit=8), problem_space)
    append_section(lines, render_named_bullets("核心逻辑", core_logic, ""))
    append_section(lines, render_named_bullets("使用与修改技巧", clean_topic_list(payload.get("usage_patterns") or payload.get("playbook"), limit=8), ""))
    append_section(lines, render_named_bullets("排障线索", clean_topic_list(payload.get("debugging_tips"), limit=8), ""))
    append_section(lines, render_named_bullets("变更约束", clean_topic_list(payload.get("change_guidelines") or payload.get("decisions"), limit=8), ""))
    append_section(lines, render_named_bullets("常见坑", clean_topic_list(payload.get("pitfalls"), limit=6), ""))
    open_questions = clean_topic_list(payload.get("open_questions"), limit=6)
    if open_questions:
        append_section(lines, render_named_bullets("待验证问题", open_questions, ""))
    return "\n".join(lines)


def remove_duplicate_text(values: list[str], text: str | None) -> list[str]:
    normalized = " ".join((text or "").split())
    return [value for value in values if " ".join(value.split()) != normalized]


def append_section(lines: list[str], section: list[str]) -> None:
    if not section:
        return
    lines.append("")
    lines.extend(section)


def render_named_bullets(title: str, values: list[str], fallback: str) -> list[str]:
    if not values and not fallback:
        return []
    lines = [f"### {title}", ""]
    if values:
        lines.extend([f"- {value}" for value in values])
    elif fallback:
        lines.append(fallback)
    return lines


def render_ai_knowledge_reference_entry(day: date, daily_dir: str, payload: dict[str, Any]) -> str:
    lines = [
        f"#### {day.isoformat()}",
        "",
        f"- Daily：[[{daily_dir}/{day.isoformat()}|{day.isoformat()}]]",
    ]
    for label, key in (
        ("证据", "evidence"),
        ("关联任务", "related_tasks"),
        ("产物路径", "artifact_paths"),
    ):
        values = clean_topic_list(payload.get(key), limit=6)
        if values:
            lines.append(f"- {label}：{'；'.join(values)}")
    return "\n".join(lines)


def parse_reference_entries(content: str) -> dict[str, str]:
    content = strip_wja_comment_markers(normalize_knowledge_headings(content))
    marker = "\n### 参考索引\n"
    if marker not in content:
        return {}
    reference_text = content.split(marker, 1)[1]
    pattern = re.compile(r"(?m)^#### (\d{4}-\d{2}-\d{2})\s*$")
    matches = list(pattern.finditer(reference_text))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(reference_text)
        result[match.group(1)] = reference_text[start:end].strip()
    return result


def strip_wja_comment_markers(content: str) -> str:
    content = re.sub(r"(?m)^<!--\s*/?wja-knowledge[^>]*-->\n?", "", content)
    content = re.sub(r"(?m)^<!--\s*/?wja[^>]*-->\n?", "", content)
    return content


def clean_topic_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def clean_topic_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        cleaned = clean_topic_text(item)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(truncate_item(cleaned, 180))
        if len(result) >= limit:
            break
    return result


def is_knowledge_worthy(task: TaskSummary) -> bool:
    if is_noise_task(task):
        return False
    return bool(task.ai_deliverables or task.ai_evidence or task.ai_artifact_paths or task.ai_impact)


def knowledge_topic(task: TaskSummary) -> str:
    project = repo_identity(task.cwd)
    if project:
        return project
    title = display_title(task)
    return title if title != "未命名任务" else "work-journal-agent"


def upsert_knowledge_entry(target: Path, *, service: str, topic: str, day: date, daily_dir: str, task: TaskSummary) -> None:
    marker = f"wja:{day.isoformat()}:{task.key}"
    start = f"<!-- {marker} -->"
    end = f"<!-- /{marker} -->"
    entry = "\n".join([start, render_knowledge_entry(day, daily_dir, task), end])
    if target.exists():
        content = target.read_text(encoding="utf-8")
    else:
        content = "\n".join(["---", "type: knowledge-topic", f'service: "{service}"', f'topic: "{topic}"', "---", "", f"# {topic}", "", "## 参考索引", ""])
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if pattern.search(content):
        content = pattern.sub(entry, content)
    else:
        content = content.rstrip() + "\n\n" + entry
    target.write_text(content.rstrip() + "\n", encoding="utf-8")


def render_knowledge_entry(day: date, daily_dir: str, task: TaskSummary) -> str:
    lines = [
        f"### {day.isoformat()} - {display_title(task)}",
        "",
        f"- Daily：[[{daily_dir}/{day.isoformat()}|{day.isoformat()}]]",
        f"- 项目：{repo_identity(task.cwd) or 'unknown'}",
    ]
    deliverables = compact_items(task.ai_deliverables or task.ai_outputs, limit=5, char_limit=160)
    if deliverables:
        lines.append(f"- 关键产出：{'；'.join(deliverables)}")
    if task.ai_impact and task.ai_impact != "暂无明确影响":
        lines.append(f"- 影响：{task.ai_impact}")
    if task.ai_evidence:
        lines.append(f"- 证据：{compact_followup_items(task.ai_evidence)}")
    if task.ai_artifact_paths:
        lines.append(f"- 产物路径：{compact_followup_items(task.ai_artifact_paths)}")
    followups = render_followup_lines(task)
    if followups:
        lines.extend(followups)
    return "\n".join(lines)


def render_followup_lines(task: TaskSummary) -> list[str]:
    lines: list[str] = []
    if task.ai_next_actions:
        lines.append(f"- 待办：{compact_followup_items(task.ai_next_actions)}")
    if task.ai_blockers:
        lines.append(f"- 阻塞：{compact_followup_items(task.ai_blockers)}")
    if task.ai_questions:
        lines.append(f"- 待确认：{compact_followup_items(task.ai_questions)}")
    if task.ai_validation_gaps:
        lines.append(f"- 验证缺口：{compact_followup_items(task.ai_validation_gaps)}")
    if task.ai_owner_hint and task.ai_owner_hint != "none":
        lines.append(f"- 建议责任方：{task.ai_owner_hint}")
    return lines


def render_task_followup_bullets(task: TaskSummary) -> list[str]:
    lines: list[str] = []
    for label, values in (
        ("待办", task.ai_next_actions),
        ("阻塞", task.ai_blockers),
        ("待确认", task.ai_questions),
        ("验证缺口", task.ai_validation_gaps),
    ):
        for value in compact_items(values, limit=5, char_limit=140):
            lines.append(f"- {label}：{value}")
    if task.ai_owner_hint and task.ai_owner_hint != "none":
        lines.append(f"- 建议责任方：{task.ai_owner_hint}")
    return lines


def compact_followup_items(items: list[str]) -> str:
    shown = compact_items(items, limit=5, char_limit=120)
    return "；".join(shown)


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
