import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from datetime import date, datetime, timezone
from pathlib import Path

from work_journal_agent.ai_cache import load_cache, prune_cache, save_cache
from work_journal_agent.cli import main
from work_journal_agent.config import StorageConfig, load_config
from work_journal_agent.events import WorkEvent, append_event, read_events
from work_journal_agent.requirements import load_daily_review, load_status, save_review_decisions
from work_journal_agent.writers.obsidian import render_daily
from work_journal_agent.merge import group_events
from work_journal_agent.requirements import apply_requirement_assignments, merge_confirmed_requirement_tasks


class SqliteStorageTests(unittest.TestCase):
    def test_migrate_storage_imports_legacy_json_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config = sqlite_config(base)
            legacy = WorkEvent.create(
                source="codex",
                event_type="user_prompt",
                summary="Codex 用户需求：迁移存储",
                occurred_at=datetime(2026, 6, 16, 9, 0, tzinfo=timezone.utc),
                raw_request="迁移存储",
                metadata={"codex_event_key": "session-a:1:user_prompt"},
            )
            config.storage.inbox_path.parent.mkdir(parents=True, exist_ok=True)
            config.storage.inbox_path.write_text(legacy.to_json_line() + "\n", encoding="utf-8")
            requirements_dir = base / "data" / "work-journal-agent" / "requirements"
            requirements_dir.mkdir(parents=True)
            (requirements_dir / "threads.json").write_text(
                json.dumps(
                    {
                        "requirements": [
                            {
                                "id": "req_demo",
                                "title": "迁移演示需求",
                                "project": "work-journal-agent",
                                "updated_at": "2026-06-16T09:00:00+00:00",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (requirements_dir / "daily").mkdir()
            (requirements_dir / "daily" / "2026-06-16.json").write_text(
                json.dumps({"date": "2026-06-16", "assignments": [], "ignored_event_ids": []}),
                encoding="utf-8",
            )
            (base / "data" / "work-journal-agent" / "state").mkdir()
            (base / "data" / "work-journal-agent" / "state" / "status.json").write_text(
                json.dumps({"pending_requirements": 2}),
                encoding="utf-8",
            )
            ai_cache_dir = base / "data" / "work-journal-agent" / "ai-cache"
            ai_cache_dir.mkdir()
            (ai_cache_dir / "2026-06-16.json").write_text(json.dumps({"tasks": [{"key": "cached"}]}), encoding="utf-8")

            migrate_args = [
                "--config",
                str(base / "config.toml"),
                "migrate-storage",
                "--legacy-inbox",
                str(config.storage.inbox_path),
                "--legacy-requirements-dir",
                str(requirements_dir),
                "--legacy-state-dir",
                str(base / "data" / "work-journal-agent" / "state"),
                "--legacy-ai-cache-dir",
                str(ai_cache_dir),
            ]
            with redirect_stdout(StringIO()):
                main(migrate_args)
                main(migrate_args)

            migrated = read_events(config.storage)
            self.assertEqual([event.raw_request for event in migrated], ["迁移存储"])
            self.assertEqual(load_daily_review(date(2026, 6, 16), storage=config.storage)["date"], "2026-06-16")
            self.assertEqual(load_status(storage=config.storage)["pending_requirements"], 2)
            self.assertEqual(load_cache(config.storage, date(2026, 6, 16))["tasks"][0]["key"], "cached")

            duplicate = WorkEvent.create(
                source="codex",
                event_type="user_prompt",
                summary="Codex 用户需求：重复",
                occurred_at=datetime(2026, 6, 16, 10, 0, tzinfo=timezone.utc),
                raw_request="重复",
                metadata={"codex_event_key": "session-a:1:user_prompt"},
            )
            append_event(config.storage, duplicate)

            self.assertEqual(len(read_events(config.storage)), 1)

    def test_requirement_decisions_and_status_are_saved_to_sqlite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = sqlite_config(base)
                event = WorkEvent.create(
                    source="codex",
                    event_type="user_prompt",
                    summary="Codex 用户需求：实现 SQLite 存储",
                    occurred_at=datetime(2026, 6, 16, 9, 0, tzinfo=timezone.utc),
                    raw_request="实现 SQLite 存储",
                    cwd="/repo/work-journal-agent",
                )
                append_event(config.storage, event)

                save_review_decisions(
                    date(2026, 6, 16),
                    [
                        {
                            "candidate_id": "cand_sqlite",
                            "title": "工作日志 SQLite 存储迁移",
                            "project": "work-journal-agent",
                            "requirement_type": "direct",
                            "status": "confirmed",
                            "event_ids": [event.id],
                        }
                    ],
                    config=config,
                )

                daily = load_daily_review(date(2026, 6, 16), storage=config.storage)
                status = load_status(storage=config.storage)

                self.assertEqual(daily["assignments"][0]["title"], "工作日志 SQLite 存储迁移")
                self.assertEqual(status["pending_requirements"], 0)
                self.assertFalse((base / "data" / "work-journal-agent" / "requirements" / "threads.json").exists())
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)

    def test_sqlite_day_query_and_confirmed_title_feed_daily(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config = sqlite_config(base)
            old_event = WorkEvent.create(
                source="codex",
                event_type="user_prompt",
                summary="昨天需求",
                occurred_at=datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc),
                raw_request="昨天需求",
                cwd="/repo/service",
            )
            today_event = WorkEvent.create(
                source="codex",
                event_type="user_prompt",
                summary="Codex 用户需求：原始标题",
                occurred_at=datetime(2026, 6, 16, 9, 0, tzinfo=timezone.utc),
                raw_request="原始标题",
                cwd="/repo/service",
            )
            append_event(config.storage, old_event)
            append_event(config.storage, today_event)

            self.assertEqual([event.id for event in read_events(config.storage, day=date(2026, 6, 16))], [today_event.id])

            save_review_decisions(
                date(2026, 6, 16),
                [
                    {
                        "candidate_id": "cand_today",
                        "title": "确认后的需求标题",
                        "project": "service",
                        "requirement_type": "direct",
                        "status": "confirmed",
                        "event_ids": [today_event.id],
                    }
                ],
                config=config,
            )
            tasks = group_events(read_events(config.storage, day=date(2026, 6, 16)), min_keyword_overlap=1)
            apply_requirement_assignments(config, date(2026, 6, 16), tasks)
            daily = render_daily(date(2026, 6, 16), merge_confirmed_requirement_tasks(tasks))

            self.assertIn("确认后的需求标题", daily)
            self.assertNotIn("Codex 用户需求：原始标题", daily)

    def test_ai_cache_uses_sqlite_and_prunes_by_retention(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            storage = StorageConfig(
                inbox_path=base / "events.jsonl",
                output_dir=base / "out",
                database_path=base / "work-journal.db",
            )

            save_cache(storage, date(2026, 6, 10), [{"key": "old"}])
            save_cache(storage, date(2026, 6, 12), [{"key": "new"}])
            prune_cache(storage, keep_days=2, today=date(2026, 6, 12))

            self.assertEqual(load_cache(storage, date(2026, 6, 10))["tasks"], [])
            self.assertEqual(load_cache(storage, date(2026, 6, 12))["tasks"][0]["key"], "new")
            self.assertFalse((base / "2026-06-12.json").exists())


def sqlite_config(base: Path):
    config_path = base / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[storage]",
                f'database_path = "{base / "work-journal.db"}"',
                f'inbox_path = "{base / "events.jsonl"}"',
                f'output_dir = "{base / "out"}"',
                "",
                "[sources.codex]",
                "enabled = false",
                "",
                "[sources.opencode]",
                "enabled = false",
                "",
                "[sources.kun]",
                "enabled = false",
                "",
                "[sources.zcode]",
                "enabled = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return load_config(config_path)


def restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
