import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from work_journal_agent.ai import ClusterReviewResult
from work_journal_agent.config import (
    AiConfig,
    AppConfig,
    ClaudeSourceConfig,
    CodexSourceConfig,
    KunSourceConfig,
    MergeConfig,
    ObsidianConfig,
    OpenCodeSourceConfig,
    PrivacyConfig,
    SourcesConfig,
    StorageConfig,
    ZCodeSourceConfig,
)
from work_journal_agent.event_semantics import SemanticSummaryResult
from work_journal_agent.events import WorkEvent, append_event
from work_journal_agent.merge import group_events
from work_journal_agent.requirements import apply_requirement_assignments, build_requirement_management_payload, build_review_payload, filter_ignored_events, load_daily_review, load_review_payload, load_threads, merge_confirmed_requirement_tasks, merge_requirement_threads, save_requirement_threads, save_review_decisions


class RequirementReviewTests(unittest.TestCase):
    def test_build_review_payload_marks_path_title_low_confidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = test_config(base)
                append_event(
                    config.storage.inbox_path,
                    event(
                        "e1",
                        "@/repo/service/src/main/java/demo/App.java",
                        raw_request="@/repo/service/src/main/java/demo/App.java",
                        cwd="/repo/service",
                    ),
                )

                payload = build_review_payload(config, date(2026, 6, 12))

                self.assertEqual(payload["summary"]["total_candidates"], 1)
                candidate = payload["candidates"][0]
                self.assertLess(candidate["confidence"], 0.85)
                self.assertIn("建议人工改名", " ".join(candidate["reasons"]))
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)

    def test_confirmed_requirement_title_is_applied_to_daily_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = test_config(base)
                work_event = event("e1", "Codex 用户需求：按方案实现", raw_request="按方案实现视觉分拣 ETL 改造", cwd="/repo/tms-flink-finance")
                append_event(config.storage.inbox_path, work_event)
                tasks = group_events([work_event], min_keyword_overlap=1)
                candidate_id = "cand_test"

                save_review_decisions(
                    date(2026, 6, 12),
                    [
                        {
                            "candidate_id": candidate_id,
                            "title": "视觉分拣 ETL 生产波次绑定关系历史查询改造",
                            "project": "tms-flink-finance",
                            "requirement_type": "plan-driven",
                            "status": "confirmed",
                            "event_ids": [work_event.id],
                            "anchors": {"implementation_files": ["EmployeeSupportWorkVisualSortingEtl.java"]},
                        }
                    ],
                    config=config,
                )

                apply_requirement_assignments(config, date(2026, 6, 12), tasks)

                self.assertEqual(tasks[0].ai_title, "视觉分拣 ETL 生产波次绑定关系历史查询改造")
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)

    def test_review_payload_can_be_loaded_without_rebuilding_candidates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = test_config(base)
                work_event = event("e1", "Codex 用户需求：按方案实现", raw_request="按方案实现视觉分拣 ETL 改造", cwd="/repo/tms-flink-finance")
                append_event(config.storage.inbox_path, work_event)

                built = build_review_payload(config, date(2026, 6, 12))
                loaded = load_review_payload(config, date(2026, 6, 12))

                self.assertEqual(loaded["summary"]["total_candidates"], built["summary"]["total_candidates"])
                self.assertEqual(loaded["candidates"][0]["candidate_id"], built["candidates"][0]["candidate_id"])

                save_review_decisions(
                    date(2026, 6, 12),
                    [
                        {
                            "candidate_id": built["candidates"][0]["candidate_id"],
                            "title": "视觉分拣 ETL 生产波次绑定关系历史查询改造",
                            "project": "tms-flink-finance",
                            "requirement_type": "plan-driven",
                            "status": "confirmed",
                            "event_ids": [work_event.id],
                        }
                    ],
                    config=config,
                )
                loaded_after_save = load_review_payload(config, date(2026, 6, 12))

                self.assertEqual(loaded_after_save["summary"]["total_candidates"], 1)
                self.assertEqual(loaded_after_save["candidates"][0]["status"], "confirmed")
                self.assertEqual(loaded_after_save["candidates"][0]["title"], "视觉分拣 ETL 生产波次绑定关系历史查询改造")
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)

    def test_filter_ignored_events_removes_confirmed_ignored_candidates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = test_config(base)
                kept = event("e1", "实现真实需求", raw_request="实现真实需求", cwd="/repo/service")
                ignored = event("e2", "你好", raw_request="你好", cwd="/repo/service")

                save_review_decisions(
                    date(2026, 6, 12),
                    [
                        {
                            "candidate_id": "cand_ignored",
                            "title": "你好",
                            "project": "service",
                            "requirement_type": "direct",
                            "status": "ignored",
                            "event_ids": [ignored.id],
                        }
                    ],
                    config=config,
                )

                filtered = filter_ignored_events(date(2026, 6, 12), [kept, ignored])

                self.assertEqual([item.id for item in filtered], [kept.id])
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)

    def test_merge_confirmed_requirement_tasks_groups_same_title_across_agents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = test_config(base)
                codex_event = event(
                    "e1",
                    "Codex 用户需求：实现模拟打标优化",
                    raw_request="实现模拟打标优化",
                    cwd="/repo/logistics-center",
                    source="codex",
                    metadata={"session_id": "codex-1"},
                )
                zcode_event = event(
                    "e2",
                    "ZCode 用户需求：补充模拟打标优化",
                    raw_request="补充模拟打标优化",
                    cwd="/repo/logistics-center",
                    source="zcode",
                    metadata={"session_id": "zcode-1"},
                )
                tasks = group_events([codex_event, zcode_event], min_keyword_overlap=1)
                save_review_decisions(
                    date(2026, 6, 12),
                    [
                        {
                            "candidate_id": "cand_codex",
                            "title": "生产计划模拟打标优化",
                            "project": "logistics-center",
                            "requirement_type": "direct",
                            "status": "confirmed",
                            "event_ids": [codex_event.id],
                        },
                        {
                            "candidate_id": "cand_zcode",
                            "title": "生产计划模拟打标优化",
                            "project": "logistics-center",
                            "requirement_type": "direct",
                            "status": "confirmed",
                            "event_ids": [zcode_event.id],
                        },
                    ],
                    config=config,
                )

                apply_requirement_assignments(config, date(2026, 6, 12), tasks)
                merged = merge_confirmed_requirement_tasks(tasks)

                self.assertEqual(len(merged), 1)
                self.assertEqual(merged[0].ai_title, "生产计划模拟打标优化")
                self.assertEqual(merged[0].sources, {"codex", "zcode"})
                self.assertEqual(merged[0].event_count, 2)
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)

    def test_existing_requirement_can_be_reused_across_days(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = test_config(base)
                first_event = event("e1", "实现分拣消息幂等", raw_request="实现分拣消息幂等", cwd="/repo/wms-out")
                second_event = event("e2", "补充分货订单流程", raw_request="补充分货订单流程", cwd="/repo/wms-out")

                save_review_decisions(
                    date(2026, 6, 12),
                    [
                        {
                            "candidate_id": "cand_first",
                            "title": "分拣操作通知数据存储与并发控制方案设计",
                            "project": "wms-out",
                            "requirement_type": "direct",
                            "status": "confirmed",
                            "event_ids": [first_event.id],
                        }
                    ],
                    config=config,
                )
                threads = load_threads(storage=config.storage)
                self.assertEqual(len(threads), 1)
                requirement_id = next(iter(threads))
                created_at = threads[requirement_id]["created_at"]

                save_review_decisions(
                    date(2026, 6, 13),
                    [
                        {
                            "candidate_id": "cand_second",
                            "requirement_id": requirement_id,
                            "title": "手误写成另一个标题",
                            "project": "wms-out",
                            "requirement_type": "debug",
                            "status": "confirmed",
                            "event_ids": [second_event.id],
                        }
                    ],
                    config=config,
                )

                threads = load_threads(storage=config.storage)
                saved_daily = load_daily_review(date(2026, 6, 13), storage=config.storage)
                daily = build_review_payload(config, date(2026, 6, 13))

                self.assertEqual(len(threads), 1)
                self.assertEqual(threads[requirement_id]["created_at"], created_at)
                self.assertEqual(saved_daily["assignments"][0]["requirement_id"], requirement_id)
                self.assertEqual(saved_daily["assignments"][0]["title"], "分拣操作通知数据存储与并发控制方案设计")
                self.assertEqual(daily["requirements"][0]["id"], requirement_id)
                self.assertEqual(daily["requirements"][0]["title"], "分拣操作通知数据存储与并发控制方案设计")
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)

    def test_requirement_management_can_create_edit_and_complete_threads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = test_config(base)

                payload = save_requirement_threads(
                    [
                        {
                            "id": "new_local",
                            "title": "手动添加的跨天需求",
                            "project": "wms-out",
                            "requirement_type": "direct",
                            "status": "in_progress",
                            "note": "先手动建档，后续确认时选择。",
                        }
                    ],
                    config=config,
                )
                requirement = payload["requirements"][0]
                created_at = requirement["created_at"]

                payload = save_requirement_threads(
                    [
                        {
                            **requirement,
                            "title": "手动添加的跨天需求（改名）",
                            "status": "completed",
                            "note": "已完成。",
                        }
                    ],
                    config=config,
                )
                requirement = payload["requirements"][0]
                review_payload = build_review_payload(config, date(2026, 6, 14))

                self.assertEqual(requirement["title"], "手动添加的跨天需求（改名）")
                self.assertEqual(requirement["status"], "completed")
                self.assertEqual(requirement["created_at"], created_at)
                self.assertEqual(build_requirement_management_payload(config)["summary"]["completed"], 1)
                self.assertEqual(review_payload["requirements"], [])
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)

    def test_same_requirement_title_is_single_thread_across_projects(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = test_config(base)

                save_review_decisions(
                    date(2026, 6, 12),
                    [
                        {
                            "candidate_id": "cand_tms",
                            "title": "鲜猪肉质检计薪",
                            "project": "tms-flink-finance",
                            "requirement_type": "review",
                            "status": "confirmed",
                            "event_ids": ["e1"],
                        },
                        {
                            "candidate_id": "cand_pfms",
                            "title": "鲜猪肉质检计薪",
                            "project": "pfms",
                            "requirement_type": "review",
                            "status": "confirmed",
                            "event_ids": ["e2"],
                        },
                    ],
                    config=config,
                )

                threads = load_threads(storage=config.storage)
                saved_daily = load_daily_review(date(2026, 6, 12), storage=config.storage)
                requirement_ids = {assignment["requirement_id"] for assignment in saved_daily["assignments"]}

                self.assertEqual(len(threads), 1)
                self.assertEqual(requirement_ids, set(threads))
                requirement = next(iter(threads.values()))
                self.assertEqual(requirement["title"], "鲜猪肉质检计薪")
                self.assertEqual(requirement["project"], "tms-flink-finance, pfms")
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)

    def test_requirement_project_is_derived_from_candidate_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = test_config(base)
                work_event = event("e1", "实现 PFMS 文档治理", raw_request="实现 PFMS 文档治理", cwd="/repo/pfms")
                append_event(config.storage.inbox_path, work_event)
                payload = build_review_payload(config, date(2026, 6, 12))
                candidate = payload["candidates"][0]

                save_review_decisions(
                    date(2026, 6, 12),
                    [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "title": "鲜猪肉质检计薪",
                            "project": "用户不应手动决定的项目",
                            "requirement_type": "review",
                            "status": "confirmed",
                            "event_ids": [work_event.id],
                        }
                    ],
                    config=config,
                )

                requirement = next(iter(load_threads(storage=config.storage).values()))

                self.assertEqual(requirement["project"], "pfms")
                self.assertEqual(requirement["projects"], ["pfms"])
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)

    def test_requirement_merge_rewrites_existing_daily_assignments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = test_config(base)

                save_review_decisions(
                    date(2026, 6, 12),
                    [
                        {
                            "candidate_id": "cand_primary",
                            "title": "目视分拣计薪二期",
                            "project": "tms-flink-finance",
                            "requirement_type": "direct",
                            "status": "confirmed",
                            "event_ids": ["e1"],
                            "anchors": {"implementation_files": ["VisualSortingEtl.java"]},
                        }
                    ],
                    config=config,
                )
                save_review_decisions(
                    date(2026, 6, 13),
                    [
                        {
                            "candidate_id": "cand_duplicate",
                            "title": "生产力目视分拣计薪二期",
                            "project": "tms-flink-finance",
                            "requirement_type": "direct",
                            "status": "confirmed",
                            "event_ids": ["e2"],
                            "anchors": {"implementation_files": ["VisualSortingPrice.java"]},
                        }
                    ],
                    config=config,
                )
                threads = load_threads(storage=config.storage)
                primary_id = next(requirement_id for requirement_id, thread in threads.items() if thread["title"] == "目视分拣计薪二期")
                duplicate_id = next(requirement_id for requirement_id, thread in threads.items() if thread["title"] == "生产力目视分拣计薪二期")

                merge_requirement_threads(primary_id, [duplicate_id], config=config)

                threads = load_threads(storage=config.storage)
                first_daily = load_daily_review(date(2026, 6, 12), storage=config.storage)
                second_daily = load_daily_review(date(2026, 6, 13), storage=config.storage)

                self.assertEqual(set(threads), {primary_id})
                self.assertEqual(first_daily["assignments"][0]["requirement_id"], primary_id)
                self.assertEqual(second_daily["assignments"][0]["requirement_id"], primary_id)
                self.assertEqual(second_daily["assignments"][0]["title"], "目视分拣计薪二期")
                self.assertEqual(
                    threads[primary_id]["anchors"]["implementation_files"],
                    ["VisualSortingEtl.java", "VisualSortingPrice.java"],
                )
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)

    def test_incremental_refresh_appends_new_events_to_existing_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = test_config(base, ai_enabled=True)
                first = event("e1", "优化 DeepSeek 聚类超时", raw_request="优化 DeepSeek 聚类超时", cwd="/repo/work-journal-agent")
                append_event(config.storage.inbox_path, first)

                with patched_requirement_ai() as calls:
                    initial = build_review_payload(config, date(2026, 6, 12))

                old_candidate = initial["candidates"][0]
                self.assertEqual(calls["cluster_event_ids"], [["e1"]])
                save_review_decisions(
                    date(2026, 6, 12),
                    [
                        {
                            **old_candidate,
                            "title": "DeepSeek 递归聚类性能优化",
                            "status": "confirmed",
                        }
                    ],
                    config=config,
                )

                second = event("e2", "继续处理 DeepSeek 聚类等待时间", raw_request="继续处理 DeepSeek 聚类等待时间", cwd="/repo/work-journal-agent")
                append_event(config.storage.inbox_path, second)
                merge_payload = {
                    "operations": [
                        {
                            "action": "append_to_existing",
                            "target_candidate_id": old_candidate["candidate_id"],
                            "new_candidate_ids": [],
                            "confidence": 0.9,
                            "reason": "新增事件继续讨论 DeepSeek 聚类等待时间。",
                        }
                    ]
                }

                def merge_response(_: object, __: str, prompt: str, **___: object) -> dict[str, object]:
                    context = json_from_prompt(prompt)
                    new_id = context["new_candidates"][0]["candidate_id"]
                    merge_payload["operations"][0]["new_candidate_ids"] = [new_id]
                    return merge_payload

                with patched_requirement_ai(merge_response=merge_response) as calls:
                    refreshed = build_review_payload(config, date(2026, 6, 12))

                self.assertEqual(calls["cluster_event_ids"], [["e2"]])
                self.assertEqual(len(refreshed["candidates"]), 1)
                candidate = refreshed["candidates"][0]
                self.assertEqual(candidate["candidate_id"], old_candidate["candidate_id"])
                self.assertEqual(candidate["status"], "confirmed")
                self.assertEqual(candidate["title"], "DeepSeek 递归聚类性能优化")
                self.assertEqual(sorted(candidate["event_ids"]), ["e1", "e2"])
                self.assertEqual(refreshed["assignments"][0]["event_ids"], ["e1", "e2"])
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)

    def test_incremental_refresh_keeps_new_candidate_separate_when_merge_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = test_config(base, ai_enabled=True)
                first = event("e1", "SQLite 存储迁移", raw_request="将 JSON 存储迁移到 SQLite", cwd="/repo/work-journal-agent")
                append_event(config.storage.inbox_path, first)
                with patched_requirement_ai():
                    initial = build_review_payload(config, date(2026, 6, 12))
                old_candidate = initial["candidates"][0]

                second = event("e2", "需求管理弹窗", raw_request="新增需求管理弹窗", cwd="/repo/work-journal-agent")
                append_event(config.storage.inbox_path, second)

                with patched_requirement_ai(merge_response=OSError("connection closed")):
                    refreshed = build_review_payload(config, date(2026, 6, 12))

                self.assertEqual(len(refreshed["candidates"]), 2)
                self.assertTrue(any(candidate["candidate_id"] == old_candidate["candidate_id"] and candidate["event_ids"] == ["e1"] for candidate in refreshed["candidates"]))
                self.assertTrue(any(candidate["event_ids"] == ["e2"] for candidate in refreshed["candidates"]))
                self.assertIn("failed", refreshed["summary"]["incremental_merge"])
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)


def test_config(base: Path, *, ai_enabled: bool = False) -> AppConfig:
    return AppConfig(
        storage=StorageConfig(inbox_path=base / "events.jsonl", output_dir=base / "out"),
        obsidian=ObsidianConfig(vault_path=None, daily_dir="Daily", task_dir="Tasks", write_task_notes=False, knowledge_dir="Knowledge", write_knowledge_notes=False),
        privacy=PrivacyConfig(max_raw_request_chars=500, store_transcript_paths=True),
        merge=MergeConfig(min_keyword_overlap=1),
        ai=AiConfig(
            enabled=ai_enabled,
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key_env="DEEPSEEK_API_KEY",
            timeout_seconds=120,
            cache_enabled=True,
            cache_retention_days=7,
            cache_dir=base / "ai-cache",
            cluster_review_enabled=True,
            cluster_review_timeout_seconds=240,
            cluster_review_min_confidence=0.75,
            knowledge_enabled=False,
        ),
        sources=SourcesConfig(
            codex=CodexSourceConfig(enabled=False, sessions_root=base / "codex"),
            claude=ClaudeSourceConfig(enabled=False, settings_path=base / "claude.json"),
            opencode=OpenCodeSourceConfig(enabled=False, storage_root=base / "opencode", plugin_path=base / "plugin.js"),
            kun=KunSourceConfig(enabled=False, storage_root=base / "kun", project_root=base),
            zcode=ZCodeSourceConfig(enabled=False, storage_root=base / "zcode"),
        ),
    )


def event(
    event_id: str,
    summary: str,
    *,
    raw_request: str | None = None,
    cwd: str,
    source: str = "codex",
    metadata: dict[str, str] | None = None,
) -> WorkEvent:
    return WorkEvent(
        id=event_id,
        source=source,
        event_type="user_prompt",
        occurred_at=datetime.fromisoformat("2026-06-12T09:00:00+08:00"),
        cwd=cwd,
        summary=summary,
        raw_request=raw_request,
        decision=None,
        files=(),
        metadata=metadata or {},
    )


def restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


class patched_requirement_ai:
    def __init__(self, merge_response: object | None = None) -> None:
        self.merge_response = merge_response or {"operations": []}
        self.cluster_event_ids: list[list[str]] = []
        self._patches: list[object] = []

    def __enter__(self) -> dict[str, list[list[str]]]:
        self._patches = [
            patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}),
            patch("work_journal_agent.requirements.enrich_task_semantics", side_effect=self._semantic),
            patch("work_journal_agent.requirements.review_task_clusters", side_effect=self._cluster),
            patch("work_journal_agent.requirements.call_deepseek_for_prompt", side_effect=self._merge),
        ]
        for item in self._patches:
            item.__enter__()
        return {"cluster_event_ids": self.cluster_event_ids}

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        for item in reversed(self._patches):
            item.__exit__(exc_type, exc, tb)

    def _semantic(self, _config: AppConfig, tasks: list[object]) -> SemanticSummaryResult:
        return SemanticSummaryResult(enabled=True, used=False, summarized=len(tasks), message=f"semantic {len(tasks)}")

    def _cluster(self, _config: AppConfig, tasks: list[object]) -> ClusterReviewResult:
        event_ids = sorted(event_id for task in tasks for event_id in task.event_ids)
        self.cluster_event_ids.append(event_ids)
        return ClusterReviewResult(enabled=True, used=False, tasks=tasks, message=f"cluster {len(tasks)}")

    def _merge(self, *args: object, **kwargs: object) -> object:
        if isinstance(self.merge_response, BaseException):
            raise self.merge_response
        if callable(self.merge_response):
            return self.merge_response(*args, **kwargs)
        return self.merge_response


def json_from_prompt(prompt: str) -> dict[str, object]:
    return __import__("json").loads(prompt[prompt.index("{") :])


if __name__ == "__main__":
    unittest.main()
