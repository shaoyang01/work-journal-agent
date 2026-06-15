import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from work_journal_agent.config import (
    AiConfig,
    AppConfig,
    ClaudeSourceConfig,
    CodexSourceConfig,
    KunSourceConfig,
    MergeConfig,
    ObsidianConfig,
    OpenCodeSourceConfig,
    PrivacyConfig,
    SourcesConfig,
    StorageConfig,
    ZCodeSourceConfig,
)
from work_journal_agent.sources.zcode import events_from_zcode_storage, import_zcode_events


class ZCodeSourceTests(unittest.TestCase):
    def test_events_from_zcode_storage_extracts_messages_tools_and_diffs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            storage_root = base / "zcode-cli"
            db_path = storage_root / "db" / "db.sqlite"
            create_zcode_db(db_path)

            events, scanned_files = events_from_zcode_storage(storage_root, config=test_config(base), day=date(2026, 6, 15))

            self.assertEqual(scanned_files, 1)
            self.assertEqual([event.event_type for event in events], ["user_prompt", "conclusion", "tool_result", "file_change"])
            self.assertEqual(events[0].source, "zcode")
            self.assertEqual(events[0].cwd, "/repo/zcode")
            self.assertEqual(events[0].raw_request, "实现 ZCode 采集")
            self.assertEqual(events[0].metadata["branch"], "feature/zcode")
            self.assertEqual(events[1].decision, "已完成 ZCode importer。")
            self.assertEqual(events[3].files, ("src/app.py", "tests/test_app.py"))

    def test_import_zcode_events_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            storage_root = base / "zcode-cli"
            create_zcode_db(storage_root / "db" / "db.sqlite")
            config = test_config(base)

            first = import_zcode_events(config, day=date(2026, 6, 15), storage_root=storage_root)
            second = import_zcode_events(config, day=date(2026, 6, 15), storage_root=storage_root)

            self.assertEqual(first.imported_events, 4)
            self.assertEqual(second.imported_events, 0)


def create_zcode_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            create table session (
                id text primary key,
                directory text not null,
                title text not null,
                time_created integer not null,
                time_updated integer not null,
                summary_files integer,
                summary_diffs text
            );
            create table message (
                id text primary key,
                session_id text not null,
                time_created integer not null,
                time_updated integer not null,
                data text not null
            );
            create table part (
                id text primary key,
                message_id text not null,
                session_id text not null,
                time_created integer not null,
                time_updated integer not null,
                data text not null
            );
            create table tool_usage (
                id text primary key,
                session_id text not null,
                turn_id text,
                tool_call_id text not null,
                tool_name text not null,
                status text not null,
                started_at integer not null,
                completed_at integer,
                exit_code integer
            );
            """
        )
        session_id = "sess_1"
        conn.execute(
            "insert into session values (?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                "/repo/zcode",
                "实现 ZCode 采集",
                epoch_ms("2026-06-15T09:00:00+08:00"),
                epoch_ms("2026-06-15T09:04:00+08:00"),
                2,
                json.dumps([{"path": "src/app.py"}, {"file": "tests/test_app.py"}], ensure_ascii=False),
            ),
        )
        insert_message(conn, session_id, "msg_user", "user", "实现 ZCode 采集", "2026-06-15T09:00:00+08:00")
        insert_message(conn, session_id, "msg_system_reminder", "user", "<system-reminder>\n只用于上下文", "2026-06-15T09:01:00+08:00")
        insert_message(conn, session_id, "msg_assistant", "assistant", "已完成 ZCode importer。", "2026-06-15T09:02:00+08:00")
        conn.execute(
            "insert into part values (?, ?, ?, ?, ?, ?)",
            (
                "part_reasoning",
                "msg_assistant",
                session_id,
                epoch_ms("2026-06-15T09:02:01+08:00"),
                epoch_ms("2026-06-15T09:02:01+08:00"),
                json.dumps({"type": "reasoning", "text": "内部推理不进入日报"}, ensure_ascii=False),
            ),
        )
        conn.execute(
            "insert into tool_usage values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "tool_1",
                session_id,
                "turn_1",
                "call_1",
                "Edit",
                "completed",
                epoch_ms("2026-06-15T09:03:00+08:00"),
                epoch_ms("2026-06-15T09:03:05+08:00"),
                0,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def insert_message(conn: sqlite3.Connection, session_id: str, message_id: str, role: str, text: str, timestamp: str) -> None:
    created = epoch_ms(timestamp)
    conn.execute(
        "insert into message values (?, ?, ?, ?, ?)",
        (
            message_id,
            session_id,
            created,
            created,
            json.dumps(
                {
                    "role": role,
                    "path": {"cwd": "/repo/zcode", "root": "/repo/zcode"},
                    "contextSnapshot": {"envInfo": {"gitBranch": "feature/zcode"}},
                },
                ensure_ascii=False,
            ),
        ),
    )
    conn.execute(
        "insert into part values (?, ?, ?, ?, ?, ?)",
        (
            f"part_{message_id}",
            message_id,
            session_id,
            created,
            created,
            json.dumps({"type": "text", "text": text}, ensure_ascii=False),
        ),
    )


def epoch_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


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
            cluster_review_timeout_seconds=240,
            cluster_review_min_confidence=0.75,
            knowledge_enabled=False,
        ),
        sources=SourcesConfig(
            codex=CodexSourceConfig(enabled=False, sessions_root=base / "codex"),
            claude=ClaudeSourceConfig(enabled=False, settings_path=base / "claude.json"),
            opencode=OpenCodeSourceConfig(enabled=False, storage_root=base / "opencode", plugin_path=base / "plugin.js"),
            kun=KunSourceConfig(enabled=False, storage_root=base / "kun", project_root=base),
            zcode=ZCodeSourceConfig(enabled=True, storage_root=base / "zcode-cli"),
        ),
    )


if __name__ == "__main__":
    unittest.main()
