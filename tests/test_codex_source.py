import json
import tempfile
import unittest
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
from work_journal_agent.sources.codex import events_from_session, import_codex_events


class CodexSourceTests(unittest.TestCase):
    def test_events_from_session_extracts_user_final_and_patch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rollout-demo.jsonl"
            write_jsonl(
                path,
                [
                    {"timestamp": "2026-06-11T01:00:00Z", "type": "session_meta", "payload": {"id": "s1", "cwd": "/repo/a"}},
                    {
                        "timestamp": "2026-06-11T01:01:00Z",
                        "type": "response_item",
                        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "帮我修复登录问题"}]},
                    },
                    {
                        "timestamp": "2026-06-11T01:02:00Z",
                        "type": "event_msg",
                        "payload": {"type": "patch_apply_end", "changes": {"/repo/a/app.py": {"type": "update"}}},
                    },
                    {
                        "timestamp": "2026-06-11T01:03:00Z",
                        "type": "response_item",
                        "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "已修复登录问题，并补充验证。"}]},
                    },
                ],
            )

            events = events_from_session(path, config=test_config(Path(temp_dir)))

            self.assertEqual([event.event_type for event in events], ["user_prompt", "tool_result", "conclusion"])
            self.assertEqual(events[1].files, ("/repo/a/app.py",))

    def test_import_codex_events_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "sessions"
            day_dir = root / "2026" / "06" / "11"
            day_dir.mkdir(parents=True)
            path = day_dir / "rollout-demo.jsonl"
            write_jsonl(
                path,
                [
                    {"timestamp": "2026-06-11T01:00:00Z", "type": "session_meta", "payload": {"id": "s1", "cwd": "/repo/a"}},
                    {
                        "timestamp": "2026-06-11T01:01:00Z",
                        "type": "response_item",
                        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "记录 Codex 自动采集"}]},
                    },
                ],
            )
            config = test_config(Path(temp_dir))

            first = import_codex_events(config, day=__import__("datetime").date(2026, 6, 11), sessions_root=root)
            second = import_codex_events(config, day=__import__("datetime").date(2026, 6, 11), sessions_root=root)

            self.assertEqual(first.imported_events, 1)
            self.assertEqual(second.imported_events, 0)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_config(base: Path) -> AppConfig:
    return AppConfig(
        storage=StorageConfig(inbox_path=base / "events.jsonl", output_dir=base / "out"),
        obsidian=ObsidianConfig(vault_path=None, daily_dir="Daily", task_dir="Tasks", write_task_notes=False),
        privacy=PrivacyConfig(max_raw_request_chars=500, store_transcript_paths=True),
        merge=MergeConfig(min_keyword_overlap=1),
        ai=AiConfig(
            enabled=False,
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
            codex=CodexSourceConfig(enabled=True, sessions_root=base / "sessions"),
            claude=ClaudeSourceConfig(enabled=False, settings_path=base / "claude-settings.json"),
            opencode=OpenCodeSourceConfig(enabled=False, storage_root=base / "opencode-storage", plugin_path=base / "opencode-plugin.js"),
        ),
    )


if __name__ == "__main__":
    unittest.main()
