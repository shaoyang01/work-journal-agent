from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .events import WorkEvent, keywords


@dataclass
class TaskSummary:
    key: str
    title: str
    day: date
    cwd: str | None
    sources: set[str] = field(default_factory=set)
    raw_requests: list[str] = field(default_factory=list)
    discussions: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    files: set[str] = field(default_factory=set)
    session_ids: set[str] = field(default_factory=set)
    event_ids: set[str] = field(default_factory=set)
    event_count: int = 0
    ai_title: str | None = None
    ai_request: str | None = None
    ai_decision: str | None = None
    ai_outputs: list[str] = field(default_factory=list)
    ai_next: str | None = None
    ai_next_actions: list[str] = field(default_factory=list)
    ai_blockers: list[str] = field(default_factory=list)
    ai_questions: list[str] = field(default_factory=list)
    ai_validation_gaps: list[str] = field(default_factory=list)
    ai_owner_hint: str | None = None

    def add(self, event: WorkEvent) -> None:
        self.event_count += 1
        self.event_ids.add(event.id)
        self.sources.add(event.source)
        session_id = event.metadata.get("session_id")
        if session_id:
            self.session_ids.add(str(session_id))
        if event.raw_request:
            self.raw_requests.append(event.raw_request)
        if event.event_type in {"user_prompt", "note", "tool_result"}:
            self.discussions.append(event.summary)
        if event.decision:
            self.decisions.append(event.decision)
        elif event.event_type in {"conclusion", "assistant_stop", "session_end"}:
            self.decisions.append(event.summary)
        self.files.update(event.files)
        event_title = title_from_summary(" ".join([event.raw_request or "", event.summary or "", event.decision or ""]))
        if should_replace_title(self.title) or is_inferred_title(event_title):
            self.title = event_title


def group_events(events: list[WorkEvent], *, min_keyword_overlap: int = 1) -> list[TaskSummary]:
    tasks: list[TaskSummary] = []
    for event in sorted(events, key=lambda item: item.occurred_at):
        matched = find_matching_task(tasks, event, min_keyword_overlap=min_keyword_overlap)
        if matched is None:
            matched = TaskSummary(
                key=task_key(event),
                title=title_from_summary(event.summary),
                day=event.occurred_at.date(),
                cwd=event.cwd,
            )
            tasks.append(matched)
        matched.add(event)
    return tasks


def find_matching_task(tasks: list[TaskSummary], event: WorkEvent, *, min_keyword_overlap: int) -> TaskSummary | None:
    event_day = event.occurred_at.date()
    event_repo = repo_identity(event.cwd)
    event_words = keywords(" ".join([event.summary, event.raw_request or "", event.decision or ""]))
    event_files = set(event.files)
    event_session_id = str(event.metadata.get("session_id") or "")
    for task in reversed(tasks):
        if task.day != event_day:
            continue
        if repo_identity(task.cwd) != event_repo:
            continue
        if event_session_id and event_session_id in task.session_ids:
            return task
        if event_files and task.files.intersection(event_files):
            return task
        task_words = keywords(" ".join([task.title, *task.raw_requests, *task.discussions, *task.decisions]))
        if len(event_words.intersection(task_words)) >= min_keyword_overlap:
            return task
        if not event_words and not task_words:
            return task
    if event.event_type in {"conclusion", "assistant_stop", "session_end"}:
        for task in reversed(tasks):
            if task.day == event_day and repo_identity(task.cwd) == event_repo:
                return task
    return None


def task_key(event: WorkEvent) -> str:
    seed = "|".join([event.occurred_at.date().isoformat(), repo_identity(event.cwd), event.summary[:80]])
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def repo_identity(cwd: str | None) -> str:
    if not cwd:
        return ""
    return Path(cwd).expanduser().resolve().name


def title_from_summary(summary: str) -> str:
    cleaned = clean_title(" ".join(summary.split()))
    inferred = infer_title(cleaned)
    if inferred:
        return inferred
    if not cleaned:
        return "未命名任务"
    return cleaned[:60]


def clean_title(value: str) -> str:
    cleaned = value
    prefixes = (
        "Codex 用户需求：",
        "Claude 用户需求：",
        "Codex 结论：",
        "Claude 结论：",
    )
    for prefix in prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def infer_title(value: str) -> str | None:
    lowered = value.lower()
    if "work-journal-agent" in lowered or "obsidian" in lowered or "工作日志" in value:
        return "建设自动工作日志采集器"
    if "wms5-server-gateway" in lowered or "checkaccess" in lowered or "无权限" in value or "裸域名" in value:
        return "排查网关 SSO 无权限与跨系统登录态"
    if "team-package" in lowered or "团队计件" in value or "包裹单价" in value:
        return "实现团队计件包裹单价配置"
    if "tms-gateway" in lowered and ("重构" in value or "方案" in value):
        return "整理 tms-gateway 重构方案文档"
    review_match = re.search(r"review一下\s+([0-9a-f]{7,40})", lowered)
    if review_match:
        return f"Review 提交 {review_match.group(1)[:12]}"
    return None


def should_replace_title(title: str) -> bool:
    stripped = title.strip()
    return (
        not stripped
        or stripped == "未命名任务"
        or stripped in {"开搞", "记录一下"}
        or stripped.startswith(("Codex 用户需求：", "Claude 用户需求："))
    )


def is_inferred_title(title: str) -> bool:
    return title in {
        "建设自动工作日志采集器",
        "排查网关 SSO 无权限与跨系统登录态",
        "实现团队计件包裹单价配置",
        "整理 tms-gateway 重构方案文档",
    } or title.startswith("Review 提交 ")
