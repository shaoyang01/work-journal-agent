import unittest
from datetime import date

from work_journal_agent.ai import apply_ai_payload, clean_text_list, task_context
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


if __name__ == "__main__":
    unittest.main()
