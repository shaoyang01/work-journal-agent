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

    def test_render_daily_prefers_important_deliverables(self):
        task = TaskSummary(
            key="k1",
            title="DeepSeek 能力扩展",
            day=date(2026, 6, 12),
            cwd="/repo/work-journal-agent",
            sources={"codex"},
            event_count=5,
            files={"/repo/work-journal-agent/src/work_journal_agent/ai.py"},
            ai_outputs=["修改 ai.py"],
            ai_deliverables=["实现重要产出识别"],
            ai_impact="日报从文件列表升级为成果说明",
            ai_evidence=["python3 -m unittest discover -s tests 通过"],
            ai_artifact_paths=["src/work_journal_agent/ai.py"],
        )

        output = render_daily(date(2026, 6, 12), [task])

        self.assertIn("- 产出：实现重要产出识别", output)
        self.assertIn("- 影响：日报从文件列表升级为成果说明", output)
        self.assertIn("- 证据：python3 -m unittest discover -s tests 通过", output)
        self.assertIn("- 产物路径：src/work_journal_agent/ai.py", output)


if __name__ == "__main__":
    unittest.main()
