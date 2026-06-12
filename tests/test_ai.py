import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from work_journal_agent.ai import (
    apply_ai_payload,
    apply_cluster_review_payload,
    clean_text_list,
    review_task_clusters,
    summarize_tasks,
    task_context,
)
from work_journal_agent.ai_cache import save_cache, task_cache_entry
from work_journal_agent.config import (
    AiConfig,
    AppConfig,
    ClaudeSourceConfig,
    CodexSourceConfig,
    MergeConfig,
    ObsidianConfig,
    OpenCodeSourceConfig,
    PrivacyConfig,
    SourcesConfig,
    StorageConfig,
)
from work_journal_agent.events import WorkEvent
from work_journal_agent.merge import TaskSummary


class AiTests(unittest.TestCase):
    def test_apply_ai_payload_updates_task(self):
        task = TaskSummary(key="k1", title="旧标题", day=date(2026, 6, 11), cwd="/repo/a")

        apply_ai_payload(
            [task],
            [
                {
                    "key": "k1",
                    "title": "排查登录异常",
                    "request": "排查登录失败",
                    "decision": "确认是旧 cookie 影响",
                    "outputs": ["补充日志"],
                    "deliverables": ["修复登录异常识别"],
                    "impact": "用户可正常回到原页面",
                    "evidence": ["补充单测通过"],
                    "artifact_paths": ["src/login.py"],
                    "next": "测试环境验证",
                }
            ],
        )

        self.assertEqual(task.ai_title, "排查登录异常")
        self.assertEqual(task.ai_outputs, ["补充日志"])
        self.assertEqual(task.ai_deliverables, ["修复登录异常识别"])
        self.assertEqual(task.ai_impact, "用户可正常回到原页面")
        self.assertEqual(task.ai_evidence, ["补充单测通过"])
        self.assertEqual(task.ai_artifact_paths, ["src/login.py"])
        self.assertEqual(task.ai_next_actions, [])

    def test_apply_ai_payload_uses_outputs_as_deliverable_fallback(self):
        task = TaskSummary(key="k1", title="旧标题", day=date(2026, 6, 11), cwd="/repo/a")

        apply_ai_payload([task], [{"key": "k1", "outputs": ["旧产出字段"]}])

        self.assertEqual(task.ai_deliverables, ["旧产出字段"])

    def test_task_context_keeps_original_request_and_latest_decision(self):
        task = TaskSummary(
            key="k1",
            title="排查登录异常",
            day=date(2026, 6, 11),
            cwd="/repo/a",
            raw_requests=["帮我排查登录异常，普通浏览器会跳到无权限页面", "好的"],
            decisions=["早期判断不完整", "最终确认是旧 cookie 污染系统标识"],
            discussions=["Codex 修改文件：1 个"],
            event_count=4,
        )

        context = task_context(task)

        self.assertEqual(context["original_request"], "帮我排查登录异常，普通浏览器会跳到无权限页面")
        self.assertEqual(context["latest_decisions"], ["早期判断不完整", "最终确认是旧 cookie 污染系统标识"])
        self.assertEqual(context["additional_requests"], [])

    def test_apply_ai_payload_updates_followup_fields(self):
        task = TaskSummary(key="k1", title="OpenCode 自动采集", day=date(2026, 6, 11), cwd="/repo/a")

        apply_ai_payload(
            [task],
            [
                {
                    "key": "k1",
                    "next_actions": ["重启 OpenCode 加载插件", "观察 inbox 是否写入", "观察 inbox 是否写入"],
                    "blockers": ["缺少真实 OpenCode 启动验证"],
                    "questions": ["是否需要发布安装说明"],
                    "validation_gaps": ["未做端到端插件事件验证"],
                    "owner_hint": "user",
                }
            ],
        )

        self.assertEqual(task.ai_next_actions, ["重启 OpenCode 加载插件", "观察 inbox 是否写入"])
        self.assertEqual(task.ai_blockers, ["缺少真实 OpenCode 启动验证"])
        self.assertEqual(task.ai_questions, ["是否需要发布安装说明"])
        self.assertEqual(task.ai_validation_gaps, ["未做端到端插件事件验证"])
        self.assertEqual(task.ai_owner_hint, "user")

    def test_apply_ai_payload_ignores_malformed_followup_fields(self):
        task = TaskSummary(key="k1", title="异常格式", day=date(2026, 6, 11), cwd="/repo/a")

        apply_ai_payload(
            [task],
            [
                {
                    "key": "k1",
                    "next_actions": "不是数组",
                    "blockers": [None, "好的", "有效阻塞"],
                    "questions": [],
                    "validation_gaps": {},
                    "owner_hint": "someone",
                }
            ],
        )

        self.assertEqual(task.ai_next_actions, [])
        self.assertEqual(task.ai_blockers, ["有效阻塞"])
        self.assertEqual(task.ai_questions, [])
        self.assertEqual(task.ai_validation_gaps, [])
        self.assertIsNone(task.ai_owner_hint)

    def test_clean_text_list_limits_and_truncates(self):
        values = ["a" * 10, "b" * 10, "a" * 10, "c" * 10]

        result = clean_text_list(values, limit=2, char_limit=5)

        self.assertEqual(result, ["aaaa…", "bbbb…"])

    def test_summarize_tasks_reuses_cache_without_deepseek_call(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = TaskSummary(key="k1", title="缓存任务", day=date(2026, 6, 12), cwd="/repo/a", event_ids={"e1"})
            save_cache(
                base / "ai-cache",
                date(2026, 6, 12),
                [
                    task_cache_entry(
                        task,
                        context=task_context(task),
                        ai_result={
                            "title": "缓存标题",
                            "deliverables": ["缓存产出"],
                            "impact": "复用缓存",
                            "evidence": [],
                            "artifact_paths": [],
                            "next_actions": ["复用缓存"],
                            "owner_hint": "agent",
                        },
                    )
                ],
            )

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}), patch("work_journal_agent.ai.call_deepseek_for_prompt") as call:
                result = summarize_tasks(test_config(base), [task])

            call.assert_not_called()
            self.assertTrue(result.used)
            self.assertEqual(task.ai_title, "缓存标题")
            self.assertEqual(task.ai_deliverables, ["缓存产出"])
            self.assertEqual(task.ai_next_actions, ["复用缓存"])

    def test_summarize_tasks_refreshes_old_cache_without_deliverables(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = TaskSummary(key="k1", title="缓存任务", day=date(2026, 6, 12), cwd="/repo/a", event_ids={"e1"})
            save_cache(
                base / "ai-cache",
                date(2026, 6, 12),
                [task_cache_entry(task, context=task_context(task), ai_result={"title": "旧缓存标题", "outputs": ["旧产出"]})],
            )

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}), patch(
                "work_journal_agent.ai.call_deepseek_for_prompt",
                return_value=[{"key": "k1", "title": "新标题", "deliverables": ["新重要产出"], "outputs": ["新重要产出"]}],
            ) as call:
                result = summarize_tasks(test_config(base), [task])

            self.assertTrue(result.used)
            self.assertEqual(task.ai_title, "新标题")
            self.assertEqual(task.ai_deliverables, ["新重要产出"])
            self.assertIn("new_task", call.call_args.args[2])

    def test_summarize_tasks_keeps_stale_cache_when_refresh_fails(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = TaskSummary(key="k1", title="缓存任务", day=date(2026, 6, 12), cwd="/repo/a", event_ids={"e1"})
            save_cache(
                base / "ai-cache",
                date(2026, 6, 12),
                [task_cache_entry(task, context=task_context(task), ai_result={"title": "旧缓存标题", "outputs": ["旧产出"]})],
            )

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}), patch(
                "work_journal_agent.ai.call_deepseek_for_prompt",
                side_effect=URLError("timeout"),
            ):
                result = summarize_tasks(test_config(base), [task])

            self.assertFalse(result.used)
            self.assertEqual(task.ai_title, "旧缓存标题")
            self.assertEqual(task.ai_deliverables, ["旧产出"])

    def test_summarize_tasks_sends_delta_when_task_has_new_events(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_task = TaskSummary(key="k1", title="缓存任务", day=date(2026, 6, 12), cwd="/repo/a", event_ids={"e1"})
            old_task.raw_requests.append("旧需求")
            save_cache(
                base / "ai-cache",
                date(2026, 6, 12),
                [
                    task_cache_entry(
                        old_task,
                        context=task_context(old_task),
                        ai_result={"title": "旧标题", "next_actions": ["旧待办"], "owner_hint": "agent"},
                    )
                ],
            )
            current = TaskSummary(key="k1", title="缓存任务", day=date(2026, 6, 12), cwd="/repo/a", event_ids={"e1", "e2"})
            current.raw_requests.extend(["旧需求", "新增需求"])

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}), patch(
                "work_journal_agent.ai.call_deepseek_for_prompt",
                return_value=[{"key": "k1", "title": "新标题", "request": "新增需求", "next_actions": ["新待办"], "owner_hint": "agent"}],
            ) as call:
                result = summarize_tasks(test_config(base), [current])

            self.assertTrue(result.used)
            self.assertEqual(current.ai_title, "新标题")
            self.assertIn("merge_delta", call.call_args.args[2])

    def test_summarize_tasks_does_not_reuse_superset_cache_after_split(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_task = TaskSummary(key="old", title="合并任务", day=date(2026, 6, 12), cwd="/repo/a", event_ids={"e1", "e2"})
            save_cache(
                base / "ai-cache",
                date(2026, 6, 12),
                [
                    task_cache_entry(
                        old_task,
                        context=task_context(old_task),
                        ai_result={"title": "旧合并摘要", "next_actions": ["旧待办"], "owner_hint": "agent"},
                    )
                ],
            )
            current = TaskSummary(key="new", title="拆分任务", day=date(2026, 6, 12), cwd="/repo/a", event_ids={"e1"})

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}), patch(
                "work_journal_agent.ai.call_deepseek_for_prompt",
                return_value=[{"key": "new", "title": "新拆分摘要"}],
            ) as call:
                result = summarize_tasks(test_config(base), [current])

            self.assertTrue(result.used)
            self.assertEqual(current.ai_title, "新拆分摘要")
            self.assertIn("new_task", call.call_args.args[2])

    def test_apply_cluster_review_payload_merges_split_tasks(self):
        first = event("e1", "实现 AI 缓存", raw_request="支持 DeepSeek 结果缓存")
        second = event("e2", "真实执行验证缓存复用", raw_request="本地跑两遍 sync")
        tasks = task_list([first], [second])

        reviewed = apply_cluster_review_payload(
            tasks,
            {"groups": [{"title": "实现 AI 缓存并验证", "event_ids": ["e1", "e2"], "confidence": 0.9, "reason": "同一目标"}]},
            min_confidence=0.75,
        )

        self.assertEqual(len(reviewed), 1)
        self.assertEqual(reviewed[0].event_ids, {"e1", "e2"})
        self.assertEqual(reviewed[0].title, "实现 AI 缓存并验证")

    def test_apply_cluster_review_payload_splits_merged_task(self):
        first = event("e1", "关闭 OpenCode 采集", raw_request="我已经不用 opencode")
        second = event("e2", "发布 v0.3.0", raw_request="发布新版本")
        tasks = task_list([first, second])

        reviewed = apply_cluster_review_payload(
            tasks,
            {
                "groups": [
                    {"title": "关闭 OpenCode 采集", "event_ids": ["e1"], "confidence": 0.86, "reason": "配置变更"},
                    {"title": "发布新版本", "event_ids": ["e2"], "confidence": 0.88, "reason": "发布动作"},
                ]
            },
            min_confidence=0.75,
        )

        self.assertEqual([task.event_ids for task in reviewed], [{"e1"}, {"e2"}])

    def test_apply_cluster_review_payload_keeps_low_confidence_groups(self):
        first = event("e1", "整理日报")
        second = event("e2", "排查登录异常")
        tasks = task_list([first], [second])

        reviewed = apply_cluster_review_payload(
            tasks,
            {"groups": [{"title": "可能相关", "event_ids": ["e1", "e2"], "confidence": 0.5, "reason": "不确定"}]},
            min_confidence=0.75,
        )

        self.assertEqual([task.event_ids for task in reviewed], [{"e1"}, {"e2"}])

    def test_review_task_clusters_falls_back_on_bad_payload(self):
        tasks = task_list([event("e1", "整理日报")], [event("e2", "发布版本")])

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}), patch(
            "work_journal_agent.ai.call_deepseek_for_prompt",
            return_value={"bad": []},
        ):
            result = review_task_clusters(test_config(Path("/tmp")), tasks)

        self.assertFalse(result.used)
        self.assertIs(result.tasks, tasks)
        self.assertIn("failed", result.message)

    def test_review_task_clusters_reuses_cached_plan(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            tasks = task_list([event("e1", "整理日报")], [event("e2", "整理缓存")])
            payload = {"groups": [{"title": "整理日报缓存", "event_ids": ["e1", "e2"], "confidence": 0.9, "reason": "同一工作流"}]}

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}), patch(
                "work_journal_agent.ai.call_deepseek_for_prompt",
                return_value=payload,
            ) as call:
                first = review_task_clusters(test_config(base), tasks)

            self.assertEqual(call.call_count, 1)
            self.assertTrue(first.used)

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}), patch("work_journal_agent.ai.call_deepseek_for_prompt") as call:
                second = review_task_clusters(test_config(base), tasks)

            call.assert_not_called()
            self.assertEqual(len(second.tasks), 1)
            self.assertIn("cached", second.message)


def test_config(base: Path) -> AppConfig:
    return AppConfig(
        storage=StorageConfig(inbox_path=base / "events.jsonl", output_dir=base / "out"),
        obsidian=ObsidianConfig(vault_path=None, daily_dir="Daily", task_dir="Tasks", write_task_notes=False),
        privacy=PrivacyConfig(max_raw_request_chars=500, store_transcript_paths=True),
        merge=MergeConfig(min_keyword_overlap=1),
        ai=AiConfig(
            enabled=True,
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key_env="DEEPSEEK_API_KEY",
            timeout_seconds=120,
            cache_enabled=True,
            cache_retention_days=7,
            cache_dir=base / "ai-cache",
            cluster_review_enabled=True,
            cluster_review_min_confidence=0.75,
        ),
        sources=SourcesConfig(
            codex=CodexSourceConfig(enabled=False, sessions_root=base / "codex"),
            claude=ClaudeSourceConfig(enabled=False, settings_path=base / "claude.json"),
            opencode=OpenCodeSourceConfig(enabled=False, storage_root=base / "opencode", plugin_path=base / "plugin.js"),
        ),
    )


def event(event_id: str, summary: str, *, raw_request: str | None = None) -> WorkEvent:
    return WorkEvent(
        id=event_id,
        source="codex",
        event_type="user_prompt",
        occurred_at=datetime.fromisoformat("2026-06-12T09:00:00+08:00"),
        cwd="/repo/work-journal-agent",
        summary=summary,
        raw_request=raw_request,
        decision=None,
        files=(),
        metadata={},
    )


def task_list(*groups: list[WorkEvent]) -> list[TaskSummary]:
    from work_journal_agent.merge import task_from_events

    return [task_from_events(list(group)) for group in groups]


if __name__ == "__main__":
    unittest.main()
