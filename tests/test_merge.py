import unittest
from datetime import datetime

from work_journal_agent.events import WorkEvent
from work_journal_agent.merge import group_events


class MergeTests(unittest.TestCase):
    def test_group_same_day_repo_and_file_overlap(self):
        first = WorkEvent.create(
            source="claude",
            event_type="user_prompt",
            occurred_at=datetime.fromisoformat("2026-06-11T09:00:00+08:00"),
            cwd="/repo/project-a",
            summary="排查 SSO 登录跳转异常",
            raw_request="登录失败后没有跳回原页面",
            files=[],
        )
        second = WorkEvent.create(
            source="codex",
            event_type="conclusion",
            occurred_at=datetime.fromisoformat("2026-06-11T10:00:00+08:00"),
            cwd="/repo/project-a",
            summary="确认 error_code 1210 分支丢失 redirect 参数",
            decision="复用原 redirectUri",
            files=["src/GatewayLoginFilter.java"],
        )
        third = WorkEvent.create(
            source="claude",
            event_type="tool_result",
            occurred_at=datetime.fromisoformat("2026-06-11T10:05:00+08:00"),
            cwd="/repo/project-a",
            summary="修改 GatewayLoginFilter",
            files=["src/GatewayLoginFilter.java"],
        )

        tasks = group_events([first, second, third], min_keyword_overlap=1)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].files, {"src/GatewayLoginFilter.java"})

    def test_group_different_repo_separately(self):
        first = WorkEvent.create(
            source="manual",
            event_type="note",
            occurred_at=datetime.fromisoformat("2026-06-11T09:00:00+08:00"),
            cwd="/repo/project-a",
            summary="整理自动日志方案",
        )
        second = WorkEvent.create(
            source="manual",
            event_type="note",
            occurred_at=datetime.fromisoformat("2026-06-11T09:30:00+08:00"),
            cwd="/repo/project-b",
            summary="整理自动日志方案",
        )

        tasks = group_events([first, second], min_keyword_overlap=1)

        self.assertEqual(len(tasks), 2)

    def test_group_same_session_before_keyword_matching(self):
        first = WorkEvent.create(
            source="codex",
            event_type="user_prompt",
            occurred_at=datetime.fromisoformat("2026-06-11T09:00:00+08:00"),
            cwd="/repo/project-a",
            summary="排查网关无权限问题",
            metadata={"session_id": "s1"},
        )
        second = WorkEvent.create(
            source="codex",
            event_type="user_prompt",
            occurred_at=datetime.fromisoformat("2026-06-11T09:30:00+08:00"),
            cwd="/repo/project-a",
            summary="那继续看一下日志关键词",
            metadata={"session_id": "s1"},
        )

        tasks = group_events([first, second], min_keyword_overlap=10)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].title, "排查网关 SSO 无权限与跨系统登录态")


if __name__ == "__main__":
    unittest.main()
