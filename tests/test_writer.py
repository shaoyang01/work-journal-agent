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

    def test_render_daily_includes_ai_followups_when_present(self):
        task = TaskSummary(
            key="k1",
            title="OpenCode 自动采集",
            day=date(2026, 6, 11),
            cwd="/repo/project-a",
            sources={"codex", "opencode"},
            event_count=4,
            ai_next_actions=["重启 OpenCode 加载插件", "观察 inbox 是否写入"],
            ai_blockers=["缺少真实 OpenCode 启动验证"],
            ai_questions=["是否需要发布安装说明"],
            ai_validation_gaps=["未做端到端插件事件验证"],
            ai_owner_hint="user",
        )

        output = render_daily(date(2026, 6, 11), [task])

        self.assertIn("- 待办：重启 OpenCode 加载插件；观察 inbox 是否写入", output)
        self.assertIn("- 阻塞：缺少真实 OpenCode 启动验证", output)
        self.assertIn("- 待确认：是否需要发布安装说明", output)
        self.assertIn("- 验证缺口：未做端到端插件事件验证", output)
        self.assertIn("- 建议责任方：user", output)

    def test_render_daily_hides_empty_ai_followups(self):
        task = TaskSummary(
            key="k1",
            title="OpenCode 自动采集",
            day=date(2026, 6, 11),
            cwd="/repo/project-a",
            sources={"opencode"},
            event_count=2,
        )

        output = render_daily(date(2026, 6, 11), [task])

        self.assertNotIn("- 待办：", output)
        self.assertNotIn("- 阻塞：", output)
        self.assertNotIn("- 待确认：", output)
        self.assertNotIn("- 验证缺口：", output)


if __name__ == "__main__":
    unittest.main()
