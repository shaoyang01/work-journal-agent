from __future__ import annotations

import json
import os
import re
import signal
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
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
from .sqlite_store import is_sqlite_storage, store_for
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


@dataclass(frozen=True)
class KnowledgeNoteResult:
    enabled: bool
    used: bool
    topics: list[dict[str, Any]]
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


def generate_knowledge_topics(config: AppConfig, tasks: list[TaskSummary]) -> KnowledgeNoteResult:
    if not config.ai.knowledge_enabled:
        return KnowledgeNoteResult(enabled=False, used=False, topics=[], message="AI knowledge notes disabled")
    if not config.obsidian.write_knowledge_notes:
        return KnowledgeNoteResult(enabled=False, used=False, topics=[], message="Knowledge notes disabled")
    if not config.ai.enabled:
        return KnowledgeNoteResult(enabled=False, used=False, topics=[], message="AI knowledge notes disabled")
    if config.ai.provider != "deepseek":
        return KnowledgeNoteResult(enabled=True, used=False, topics=[], message=f"Unsupported AI provider: {config.ai.provider}")
    api_key = os.environ.get(config.ai.api_key_env)
    if not api_key:
        return KnowledgeNoteResult(enabled=True, used=False, topics=[], message=f"Missing API key env: {config.ai.api_key_env}")
    candidates = [task for task in tasks if knowledge_task_context(task)]
    if not candidates:
        return KnowledgeNoteResult(enabled=True, used=False, topics=[], message="AI knowledge notes skipped")
    try:
        context = [knowledge_task_context(task) for task in candidates]
        input_hash = context_hash({"knowledge_schema": 5, "knowledge": context})
        cached = load_knowledge_cache(config, tasks, input_hash)
        if cached is not None:
            topics = clean_knowledge_topics(cached)
            return KnowledgeNoteResult(enabled=True, used=True, topics=topics, message=f"AI knowledge notes reused cache ({len(topics)} topic(s))")
        payload = call_deepseek_for_prompt(config, api_key, build_knowledge_prompt(context))
        save_knowledge_cache(config, tasks, input_hash, payload)
        topics = clean_knowledge_topics(payload)
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as exc:
        return KnowledgeNoteResult(enabled=True, used=False, topics=[], message=f"AI knowledge notes failed: {exc}")
    return KnowledgeNoteResult(enabled=True, used=True, topics=topics, message=f"AI knowledge notes generated {len(topics)} topic(s)")


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
        payload = call_deepseek_for_prompt(
            config,
            api_key,
            build_cluster_review_prompt_from_context(review_context),
            timeout_seconds=config.ai.cluster_review_timeout_seconds,
        )
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
    cache_target = config.storage if is_sqlite_storage(config.storage) else config.ai.cache_dir
    cache = load_cache(cache_target, day)
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
        cache_target,
        day,
        [task_cache_entry(task, context=contexts_by_key[task.key], ai_result=ai_result_from_task(task)) for task in tasks],
    )
    prune_cache(cache_target, keep_days=config.ai.cache_retention_days, today=day)
    return len(request_items)


def cached_ai_result_has_deliverables(result: dict[str, Any]) -> bool:
    return all(key in result for key in ("deliverables", "impact", "evidence", "artifact_paths"))


def call_deepseek(config: AppConfig, api_key: str, tasks: list[TaskSummary]) -> Any:
    return call_deepseek_for_prompt(config, api_key, build_prompt(tasks))


def call_deepseek_for_prompt(config: AppConfig, api_key: str, prompt: str, *, timeout_seconds: int | None = None) -> Any:
    timeout = timeout_seconds if timeout_seconds is not None else config.ai.timeout_seconds
    body = {
        "model": config.ai.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个严谨的工作日志整理助手。只根据输入摘要整理，不编造。"
                    "输出必须严格遵守用户要求的 JSON 结构，不要输出 Markdown。"
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
    with hard_timeout(timeout):
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return parse_json_content(content)


@contextmanager
def hard_timeout(seconds: int):
    if seconds <= 0 or not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        yield
        return

    def timeout_handler(signum, frame):
        raise TimeoutError(f"DeepSeek request exceeded {seconds}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    signal.signal(signal.SIGALRM, timeout_handler)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


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


def build_knowledge_prompt(context: list[dict[str, Any]]) -> str:
    return (
        "请从以下每日任务摘要中识别值得沉淀为 Obsidian 代码库知识库的内容。\n"
        "要求：\n"
        "1. 只输出 JSON 对象，字段 topics。\n"
        "2. topics 最多 5 个；没有值得沉淀的内容则输出空数组。\n"
        "3. 每个 topic 包含 service,topic,title,code_locations,core_logic,usage_patterns,debugging_tips,change_guidelines,pitfalls,open_questions,evidence,related_tasks,artifact_paths,tags。\n"
        "4. service 必须来自输入任务的 project 字段，表示代码库或服务名；不同 service 不要混在同一个 topic。\n"
        "5. topic 是 service 内稳定的代码库逻辑、业务规则、架构约束、排障技巧、测试门禁或实现约定；不要包含 service 名前缀，不要使用当天任务标题。\n"
        "6. 只沉淀当前代码库长期有效的理解：例如模块职责、调用链、配置约定、兼容性边界、迁移检查项、排障路径、容易误判的业务规则。\n"
        "7. 禁止把“完成了某功能、生成了某文档、发布了某版本、跑过某测试”写进正文；这些只能放在 evidence 或 related_tasks。\n"
        "8. code_locations 写代码位置和职责，例如 类/方法/配置文件 -> 作用，不要只写文件名。\n"
        "9. core_logic 写这个代码库当前已经能从输入证据中确认的核心逻辑，不要写任务完成情况。\n"
        "10. usage_patterns 写下次修改、接入、迁移或排查时可复用的操作技巧。\n"
        "11. debugging_tips 写定位问题的路径、优先检查点和容易误判之处。\n"
        "12. change_guidelines 写修改这个代码库时应遵守的约定、兼容性边界和测试门禁。\n"
        "13. pitfalls 写踩坑、风险和预防方式；open_questions 只写仍需验证的长期问题，没有输出空数组。\n"
        "14. evidence 只写输入中出现的测试、真实执行、commit/tag、路径或结论证据。\n"
        "15. artifact_paths 只放重要相对路径或短路径，禁止完整源码、完整 diff、token、密钥。\n"
        "16. related_tasks 使用输入任务 title 或 key，仅用于参考索引，不要让正文像任务摘要。\n"
        "17. 如果输入缺少 code_evidence，或者只能写成任务摘要，宁可不要输出这个 topic。\n\n"
        + json.dumps(context, ensure_ascii=False)
    )


def knowledge_task_context(task: TaskSummary) -> dict[str, Any]:
    values = {
        "key": task.key,
        "title": task.ai_title or task.title,
        "project": repo_identity(task.cwd),
        "request": task.ai_request or first_meaningful(task.raw_requests, char_limit=240),
        "decision": task.ai_decision or "",
        "deliverables": compact_items(task.ai_deliverables or task.ai_outputs, limit=5, char_limit=180),
        "impact": task.ai_impact or "",
        "evidence": compact_items(task.ai_evidence, limit=5, char_limit=180),
        "artifact_paths": compact_items(task.ai_artifact_paths or relative_files(task), limit=8, char_limit=160),
        "code_evidence": collect_code_evidence(task),
        "next_actions": compact_items(task.ai_next_actions, limit=5, char_limit=160),
        "blockers": compact_items(task.ai_blockers, limit=5, char_limit=160),
    }
    has_signal = any(values[key] for key in ("code_evidence", "deliverables", "impact", "evidence", "artifact_paths"))
    return values if has_signal else {}


def collect_code_evidence(task: TaskSummary) -> list[dict[str, Any]]:
    cwd = Path(task.cwd).expanduser().resolve() if task.cwd else None
    candidates = unique(task.ai_artifact_paths + relative_files(task))
    evidence: list[dict[str, Any]] = []
    query = " ".join(
        [
            task.ai_title or task.title,
            task.ai_request or "",
            task.ai_decision or "",
            " ".join(task.ai_deliverables),
            " ".join(task.ai_evidence),
        ]
    )
    for raw_path in candidates:
        path = resolve_artifact_path(raw_path, cwd)
        if not path or not path.exists() or not path.is_file() or not is_supported_knowledge_file(path):
            continue
        item = code_evidence_for_file(path, cwd=cwd, query=query)
        if item:
            evidence.append(item)
        if len(evidence) >= 4:
            break
    return evidence


def resolve_artifact_path(value: str, cwd: Path | None) -> Path | None:
    if not value or "\x00" in value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute() and cwd:
        path = cwd / path
    try:
        return path.resolve()
    except OSError:
        return None


def is_supported_knowledge_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in {
        ".java",
        ".kt",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".go",
        ".sql",
        ".xml",
        ".yml",
        ".yaml",
        ".toml",
        ".properties",
        ".md",
        ".html",
        ".gradle",
    }:
        return True
    return path.name in {"pom.xml", "build.gradle", "settings.gradle"}


def code_evidence_for_file(path: Path, *, cwd: Path | None, query: str) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > 256_000:
            return None
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    text = redact_sensitive_lines(text)
    if not text.strip():
        return None
    relative_path = str(path)
    if cwd:
        try:
            relative_path = str(path.relative_to(cwd))
        except ValueError:
            pass
    return {
        "path": relative_path,
        "kind": path.suffix.lower().lstrip(".") or path.name,
        "symbols": extract_symbols(text, path),
        "snippets": extract_relevant_snippets(text, query=query, limit=4, char_limit=1800),
    }


def redact_sensitive_lines(text: str) -> str:
    sensitive = re.compile(r"(?i)(api[_-]?key|secret|token|password|authorization|private[_-]?key|access[_-]?key)")
    lines: list[str] = []
    for line in text.splitlines():
        if sensitive.search(line):
            lines.append("[redacted sensitive line]")
        else:
            lines.append(line)
    return "\n".join(lines)


def extract_symbols(text: str, path: Path) -> list[str]:
    patterns = [
        r"\b(?:class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"\b(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\], ?]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*\{",
        r"\bdef\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        r"\bCREATE\s+TABLE\s+`?([A-Za-z_][A-Za-z0-9_]*)`?",
    ]
    result: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            name = match.group(1)
            if name not in result:
                result.append(name)
            if len(result) >= 8:
                return result
    return result


def extract_relevant_snippets(text: str, *, query: str, limit: int, char_limit: int) -> list[str]:
    lines = text.splitlines()
    terms = knowledge_terms(query)
    declaration = re.compile(r"\b(class|interface|enum|def|function|CREATE TABLE|public|private|protected)\b", re.I)
    selected_indexes: list[int] = []
    for index, line in enumerate(lines):
        normalized = line.lower()
        if any(term in normalized for term in terms) or declaration.search(line):
            selected_indexes.append(index)
        if len(selected_indexes) >= limit:
            break
    snippets: list[str] = []
    used_ranges: list[range] = []
    for index in selected_indexes:
        start = max(0, index - 2)
        end = min(len(lines), index + 3)
        current_range = range(start, end)
        if any(ranges_overlap(current_range, used) for used in used_ranges):
            continue
        used_ranges.append(current_range)
        snippet = "\n".join(f"{line_no + 1}: {truncate_text(lines[line_no], 180)}" for line_no in current_range if lines[line_no].strip())
        if snippet:
            snippets.append(truncate_text(snippet, char_limit))
    if snippets:
        return snippets
    fallback_lines = [f"{idx + 1}: {truncate_text(line, 180)}" for idx, line in enumerate(lines[:40]) if line.strip()]
    return [truncate_text("\n".join(fallback_lines[:20]), char_limit)] if fallback_lines else []


def knowledge_terms(text: str) -> list[str]:
    ascii_terms = [term for term in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text.lower()) if len(term) >= 4]
    chinese_terms = [term for term in re.findall(r"[\u4e00-\u9fff]{2,}", text) if len(term) >= 2]
    return unique(ascii_terms + chinese_terms)[:20]


def ranges_overlap(left: range, right: range) -> bool:
    return left.start < right.stop and right.start < left.stop


def knowledge_cache_path(config: AppConfig, tasks: list[TaskSummary]) -> Path:
    day = tasks[0].day
    return config.ai.cache_dir / "knowledge" / f"{day.isoformat()}.json"


def load_knowledge_cache(config: AppConfig, tasks: list[TaskSummary], input_hash: str) -> Any | None:
    if not config.ai.cache_enabled:
        return None
    if is_sqlite_storage(config.storage):
        store = store_for(config.storage)
        with store.connect() as conn:
            payload = store.load_ai_cache(conn, "knowledge", tasks[0].day)
        if not isinstance(payload, dict) or payload.get("input_hash") != input_hash:
            return None
        return payload.get("knowledge")
    path = knowledge_cache_path(config, tasks)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("input_hash") != input_hash:
        return None
    return payload.get("knowledge")


def save_knowledge_cache(config: AppConfig, tasks: list[TaskSummary], input_hash: str, knowledge: Any) -> None:
    if not config.ai.cache_enabled:
        return
    if is_sqlite_storage(config.storage):
        payload = {
            "date": tasks[0].day.isoformat(),
            "input_hash": input_hash,
            "knowledge": knowledge,
        }
        store = store_for(config.storage)
        with store.connect() as conn:
            store.save_ai_cache(conn, "knowledge", tasks[0].day, payload)
        prune_cache(config.storage, keep_days=config.ai.cache_retention_days, today=tasks[0].day, namespace="knowledge")
        return
    path = knowledge_cache_path(config, tasks)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": tasks[0].day.isoformat(),
        "input_hash": input_hash,
        "knowledge": knowledge,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    prune_cache(path.parent, keep_days=config.ai.cache_retention_days, today=tasks[0].day)


def clean_knowledge_topics(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("AI knowledge response must be a JSON object")
    topics = payload.get("topics")
    if not isinstance(topics, list):
        raise ValueError("AI knowledge response must contain topics")
    result: list[dict[str, Any]] = []
    for item in topics[:5]:
        if not isinstance(item, dict):
            continue
        topic = clean_text(item.get("topic")) or clean_text(item.get("title"))
        if not topic:
            continue
        core_logic = clean_knowledge_list(item.get("core_logic"), limit=8, char_limit=220)
        result.append(
            {
                "service": clean_text(item.get("service")) or clean_text(item.get("project")) or clean_text(item.get("codebase")) or "",
                "topic": topic,
                "title": clean_text(item.get("title")) or topic,
                "summary": clean_text(item.get("summary")) or "",
                "problem_space": clean_text(item.get("problem_space")) or clean_text(item.get("summary")) or (core_logic[0] if core_logic else ""),
                "code_locations": clean_knowledge_list(item.get("code_locations"), limit=8, char_limit=180),
                "core_logic": core_logic,
                "usage_patterns": clean_knowledge_list(item.get("usage_patterns") or item.get("playbook"), limit=8, char_limit=220),
                "debugging_tips": clean_knowledge_list(item.get("debugging_tips"), limit=8, char_limit=220),
                "change_guidelines": clean_knowledge_list(item.get("change_guidelines"), limit=8, char_limit=220),
                "durable_insights": clean_knowledge_list(item.get("durable_insights") or item.get("key_points"), limit=8, char_limit=180),
                "playbook": clean_knowledge_list(item.get("playbook"), limit=8, char_limit=220),
                "decisions": clean_knowledge_list(item.get("decisions"), limit=6, char_limit=200),
                "pitfalls": clean_knowledge_list(item.get("pitfalls"), limit=6, char_limit=200),
                "open_questions": clean_knowledge_list(item.get("open_questions"), limit=6, char_limit=160),
                "evidence": clean_knowledge_list(item.get("evidence"), limit=6, char_limit=180),
                "related_tasks": clean_knowledge_list(item.get("related_tasks"), limit=6, char_limit=120),
                "artifact_paths": clean_knowledge_list(item.get("artifact_paths"), limit=8, char_limit=160),
                "tags": clean_knowledge_list(item.get("tags"), limit=8, char_limit=60),
            }
        )
    return result


def clean_knowledge_list(value: object, *, limit: int, char_limit: int) -> list[str]:
    single = clean_knowledge_item(value)
    if single:
        return [truncate_text(single, char_limit)]
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        cleaned = clean_knowledge_item(item)
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


def clean_knowledge_item(value: object) -> str | None:
    cleaned = clean_text(value)
    if cleaned:
        return cleaned
    if not isinstance(value, dict):
        return None
    labels = {
        "scenario": "场景",
        "when": "场景",
        "action": "做法",
        "practice": "做法",
        "decision": "决策",
        "rationale": "理由",
        "tradeoff": "取舍",
        "risk": "风险",
        "pitfall": "坑点",
        "prevention": "预防",
        "caveat": "注意",
        "question": "问题",
        "evidence": "证据",
        "path": "路径",
    }
    parts: list[str] = []
    for key, label in labels.items():
        text = clean_text(value.get(key))
        if text:
            parts.append(f"{label}：{text}")
    if not parts:
        for key in sorted(value):
            text = clean_text(value.get(key))
            if text:
                parts.append(f"{key}：{text}")
    return "；".join(parts) if parts else None


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
        "6. branch 不同通常代表不同需求；除非用户输入明确说明是同一需求跨分支处理，否则不要合并。\n"
        "7. 低置信度时保留原聚类：输出原任务对应的 event_ids，confidence 低于 0.75，并说明原因。\n"
        "8. 不要输出 Markdown，不要输出解释性正文。\n\n"
        + json.dumps(context, ensure_ascii=False)
    )


def cluster_review_cache_path(config: AppConfig, tasks: list[TaskSummary]) -> Path:
    day = tasks[0].day
    return config.ai.cache_dir / "cluster-review" / f"{day.isoformat()}.json"


def load_cluster_review_cache(config: AppConfig, tasks: list[TaskSummary], input_hash: str) -> Any | None:
    if not config.ai.cache_enabled:
        return None
    if is_sqlite_storage(config.storage):
        store = store_for(config.storage)
        with store.connect() as conn:
            payload = store.load_ai_cache(conn, "cluster-review", tasks[0].day)
        if not isinstance(payload, dict) or payload.get("input_hash") != input_hash:
            return None
        return payload.get("review")
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
    if is_sqlite_storage(config.storage):
        payload = {
            "date": tasks[0].day.isoformat(),
            "input_hash": input_hash,
            "review": review,
        }
        store = store_for(config.storage)
        with store.connect() as conn:
            store.save_ai_cache(conn, "cluster-review", tasks[0].day, payload)
        prune_cache(config.storage, keep_days=config.ai.cache_retention_days, today=tasks[0].day, namespace="cluster-review")
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
                "event_count": task.event_count,
                "event_ids": sorted(task.event_ids),
                "session_ids": sorted(task.session_ids),
                "branches": sorted(task.branches),
                "files": compact_items(relative_files(task), limit=8, char_limit=140),
                "primary_request": first_meaningful(task.raw_requests, char_limit=260),
                "recent_decisions": select_latest_meaningful(task.decisions, limit=2, char_limit=180),
                "events": [event_review_context(event) for event in cluster_review_events(task)],
            }
        )
    return result


def cluster_review_events(task: TaskSummary) -> list[WorkEvent]:
    selected: list[WorkEvent] = []
    for event in task.events:
        if event.event_type not in {"user_prompt", "conclusion", "assistant_stop", "session_end", "note"}:
            continue
        text = " ".join([event.raw_request or "", event.summary or "", event.decision or ""])
        if is_noise_item(text) or is_low_value_text(text):
            continue
        selected.append(event)
        if len(selected) >= 4:
            return selected
    return task.events[: min(3, len(task.events))]


def event_review_context(event: WorkEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "source": event.source,
        "type": event.event_type,
        "summary": truncate_text(event.summary, 140),
        "raw_request": truncate_text(event.raw_request or "", 160),
        "decision": truncate_text(event.decision or "", 140),
        "files": [truncate_text(Path(file).name, 60) for file in event.files[:4]],
        "session_id": str(event.metadata.get("session_id") or ""),
        "branch": str(event.metadata.get("branch") or event.metadata.get("git_branch") or ""),
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
