import unittest

from datetime import date

from work_journal_agent.merge import TaskSummary
from work_journal_agent.writers.obsidian import compact_items, render_daily


class WriterTests(unittest.TestCase):
    def test_compact_items_filters_noise_and_limits_output(self):
        items = [
            "<skill> noisy",
            "第一条有效内容",
            "第二条有效内容",
            "第三条有效内容",
        ]

        self.assertEqual(compact_items(items, limit=2), ["第一条有效内容", "第二条有效内容", "另有 1 条已折叠。"])

    def test_compact_items_truncates_long_text(self):
        result = compact_items(["a" * 20], char_limit=10)

        self.assertEqual(result, ["aaaaaaaaa…"])

    def test_render_daily_uses_overview_and_brief_details(self):
        task = TaskSummary(
            key="k1",
            title="排查登录异常",
            day=date(2026, 6, 11),
            cwd="/repo/project-a",
            sources={"codex"},
            raw_requests=["帮我排查登录异常"],
            decisions=["根因是旧 cookie 污染系统标识"],
            files={"/repo/project-a/src/app.py"},
            event_count=3,
        )

        output = render_daily(date(2026, 6, 11), [task])

        self.assertIn("## 今日概览", output)
        self.assertIn("## 任务详情", output)
        self.assertIn("- 需求：帮我排查登录异常", output)
        self.assertIn("- 产出：src/app.py", output)
        self.assertNotIn("### 讨论方案", output)

    def test_render_daily_filters_test_task(self):
        task = TaskSummary(
            key="k1",
            title="测试 work-journal-agent 配置完成",
            day=date(2026, 6, 11),
            cwd="/repo/project-a",
            sources={"manual"},
            event_count=1,
        )

        output = render_daily(date(2026, 6, 11), [task])

        self.assertIn("今天没有可归档的工作事件。", output)


if __name__ == "__main__":
    unittest.main()
