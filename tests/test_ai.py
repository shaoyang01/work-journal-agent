import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from work_journal_agent.ai import apply_ai_payload, clean_text_list, summarize_tasks, task_context
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
                    "next": "测试环境验证",
                }
            ],
        )

        self.assertEqual(task.ai_title, "排查登录异常")
        self.assertEqual(task.ai_outputs, ["补充日志"])
        self.assertEqual(task.ai_next_actions, [])

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
                        ai_result={"title": "缓存标题", "next_actions": ["复用缓存"], "owner_hint": "agent"},
                    )
                ],
            )

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}), patch("work_journal_agent.ai.call_deepseek_for_prompt") as call:
                result = summarize_tasks(test_config(base), [task])

            call.assert_not_called()
            self.assertTrue(result.used)
            self.assertEqual(task.ai_title, "缓存标题")
            self.assertEqual(task.ai_next_actions, ["复用缓存"])

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
            timeout_seconds=30,
            cache_enabled=True,
            cache_retention_days=7,
            cache_dir=base / "ai-cache",
        ),
        sources=SourcesConfig(
            codex=CodexSourceConfig(enabled=False, sessions_root=base / "codex"),
            claude=ClaudeSourceConfig(enabled=False, settings_path=base / "claude.json"),
            opencode=OpenCodeSourceConfig(enabled=False, storage_root=base / "opencode", plugin_path=base / "plugin.js"),
        ),
    )


if __name__ == "__main__":
    unittest.main()
