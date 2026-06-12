import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

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
from work_journal_agent.sources.opencode import event_from_hook_payload, events_from_storage, import_opencode_events


class OpenCodeSourceTests(unittest.TestCase):
    def test_events_from_storage_extracts_messages_tools_patches_and_diffs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "storage"
            write_json(
                root / "message" / "ses_1" / "msg_user.json",
                {
                    "id": "msg_user",
                    "role": "user",
                    "sessionID": "ses_1",
                    "path": "/repo/a",
                    "time": {"created": epoch_ms("2026-06-11T01:00:00+00:00")},
                },
            )
            write_json(
                root / "part" / "msg_user" / "prt_user_text.json",
                {
                    "id": "prt_user_text",
                    "messageID": "msg_user",
                    "sessionID": "ses_1",
                    "type": "text",
                    "text": "帮我支持 OpenCode 工作日志采集",
                },
            )
            write_json(
                root / "message" / "ses_1" / "msg_assistant.json",
                {
                    "id": "msg_assistant",
                    "role": "assistant",
                    "sessionID": "ses_1",
                    "path": "/repo/a",
                    "time": {"created": epoch_ms("2026-06-11T01:05:00+00:00")},
                },
            )
            write_json(
                root / "part" / "msg_assistant" / "prt_assistant_text.json",
                {
                    "id": "prt_assistant_text",
                    "messageID": "msg_assistant",
                    "sessionID": "ses_1",
                    "type": "text",
                    "text": "已完成 OpenCode importer，并补充测试。",
                },
            )
            write_json(
                root / "part" / "msg_assistant" / "prt_tool.json",
                {
                    "id": "prt_tool",
                    "messageID": "msg_assistant",
                    "sessionID": "ses_1",
                    "type": "tool",
                    "tool": "edit",
                    "state": {"status": "completed", "input": {"filePath": "/repo/a/app.py"}},
                    "time": {"end": epoch_ms("2026-06-11T01:06:00+00:00")},
                },
            )
            write_json(
                root / "part" / "msg_assistant" / "prt_patch.json",
                {
                    "id": "prt_patch",
                    "messageID": "msg_assistant",
                    "sessionID": "ses_1",
                    "type": "patch",
                    "files": [{"path": "app.py"}, {"path": "tests/test_app.py"}],
                    "time": {"end": epoch_ms("2026-06-11T01:07:00+00:00")},
                },
            )
            diff_path = root / "session_diff" / "ses_1.json"
            write_json(
                diff_path,
                [
                    {
                        "file": "app.py",
                        "additions": 2,
                        "deletions": 1,
                        "before": "old content must not be stored",
                        "after": "new content must not be stored",
                    }
                ],
            )
            os.utime(diff_path, (epoch_seconds("2026-06-11T01:08:00+00:00"), epoch_seconds("2026-06-11T01:08:00+00:00")))

            events, scanned_files = events_from_storage(root, config=test_config(Path(temp_dir)), day=date(2026, 6, 11))

            self.assertEqual(scanned_files, 7)
            self.assertEqual([event.event_type for event in events], ["user_prompt", "conclusion", "tool_result", "file_change", "file_change"])
            self.assertEqual(events[0].raw_request, "帮我支持 OpenCode 工作日志采集")
            self.assertEqual(events[2].files, ("/repo/a/app.py",))
            self.assertEqual(events[3].files, ("app.py", "tests/test_app.py"))
            self.assertNotIn("old content must not be stored", json.dumps(events[-1].metadata, ensure_ascii=False))

    def test_import_opencode_events_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "storage"
            write_json(
                root / "message" / "ses_1" / "msg_user.json",
                {
                    "id": "msg_user",
                    "role": "user",
                    "sessionID": "ses_1",
                    "time": {"created": epoch_ms("2026-06-11T01:00:00+00:00")},
                    "summary": "记录 OpenCode 自动采集",
                },
            )
            config = test_config(Path(temp_dir))

            first = import_opencode_events(config, day=date(2026, 6, 11), storage_root=root)
            second = import_opencode_events(config, day=date(2026, 6, 11), storage_root=root)

            self.assertEqual(first.imported_events, 1)
            self.assertEqual(second.imported_events, 0)

    def test_hook_payload_maps_file_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            event = event_from_hook_payload(
                {
                    "directory": "/repo/a",
                    "event": {
                        "type": "file.edited",
                        "sessionID": "ses_1",
                        "filePath": "/repo/a/app.py",
                        "timestamp": "2026-06-11T01:00:00Z",
                    },
                },
                config=test_config(Path(temp_dir)),
            )

            self.assertEqual(event.source, "opencode")
            self.assertEqual(event.event_type, "file_change")
            self.assertEqual(event.files, ("/repo/a/app.py",))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def epoch_ms(value: str) -> int:
    return int(epoch_seconds(value) * 1000)


def epoch_seconds(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def test_config(base: Path) -> AppConfig:
    return AppConfig(
        storage=StorageConfig(inbox_path=base / "events.jsonl", output_dir=base / "out"),
        obsidian=ObsidianConfig(vault_path=None, daily_dir="Daily", task_dir="Tasks", write_task_notes=False, knowledge_dir="Knowledge", write_knowledge_notes=False),
        privacy=PrivacyConfig(max_raw_request_chars=500, store_transcript_paths=True),
        merge=MergeConfig(min_keyword_overlap=1),
        ai=AiConfig(
            enabled=False,
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
            knowledge_enabled=False,
        ),
        sources=SourcesConfig(
            codex=CodexSourceConfig(enabled=False, sessions_root=base / "sessions"),
            claude=ClaudeSourceConfig(enabled=False, settings_path=base / "claude-settings.json"),
            opencode=OpenCodeSourceConfig(enabled=True, storage_root=base / "storage", plugin_path=base / "opencode-plugin.js"),
        ),
    )


if __name__ == "__main__":
    unittest.main()
