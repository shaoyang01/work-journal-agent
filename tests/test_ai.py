import unittest
from datetime import date

from work_journal_agent.ai import apply_ai_payload, task_context
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


if __name__ == "__main__":
    unittest.main()
