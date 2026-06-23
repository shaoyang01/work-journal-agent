import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from work_journal_agent.ai_run_tracker import TaskRunAlreadyActive
from work_journal_agent.cli import main
from work_journal_agent.sources.codex import CodexImportResult
from work_journal_agent.sources.opencode import OpenCodeImportResult


class CliSourcesTests(unittest.TestCase):
    def test_sync_skips_disabled_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = write_config(Path(temp_dir), codex_enabled=False, opencode_enabled=False)

            with patch("work_journal_agent.cli.collect_new_codex_events") as codex, patch("work_journal_agent.cli.collect_new_opencode_events") as opencode:
                with redirect_stdout(StringIO()):
                    main(["--config", str(config_path), "sync", "--date", "2026-06-12", "--dry-run"])

            codex.assert_not_called()
            opencode.assert_not_called()

    def test_sync_imports_enabled_sources_with_configured_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config_path = write_config(base, codex_enabled=True, opencode_enabled=True)

            with patch("work_journal_agent.cli.collect_new_codex_events", return_value=CodexImportResult(0, 0)) as codex, patch(
                "work_journal_agent.cli.collect_new_opencode_events", return_value=OpenCodeImportResult(0, 0)
            ) as opencode:
                with redirect_stdout(StringIO()):
                    main(["--config", str(config_path), "sync", "--date", "2026-06-12", "--dry-run"])

            self.assertEqual(codex.call_args.kwargs["sessions_root"], base / "codex")
            self.assertEqual(opencode.call_args.kwargs["storage_root"], base / "opencode")

    def test_sync_does_not_generate_knowledge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = write_config(Path(temp_dir), codex_enabled=False, opencode_enabled=False)

            with patch("work_journal_agent.cli.generate_knowledge_topics") as knowledge:
                with redirect_stdout(StringIO()):
                    main(["--config", str(config_path), "sync", "--date", "2026-06-12", "--dry-run"])

            knowledge.assert_not_called()

    def test_sync_imports_events_even_when_requirement_refresh_is_busy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = write_config(Path(temp_dir), codex_enabled=True, opencode_enabled=False)
            active_run = {"id": "run-1", "run_kind": "requirement_review"}

            with patch("work_journal_agent.cli.import_codex_events", return_value=CodexImportResult(scanned_files=1, imported_events=2)) as codex, patch(
                "work_journal_agent.cli.refresh_requirement_candidates", side_effect=TaskRunAlreadyActive(active_run)
            ):
                with redirect_stdout(StringIO()) as stdout:
                    main(["--config", str(config_path), "sync", "--date", "2026-06-12"])

            codex.assert_called_once()
            output = stdout.getvalue()
            self.assertIn("Imported Codex events: 2 from 1 files", output)
            self.assertIn("已有任务正在执行：requirement_review run-1", output)

    def test_generate_knowledge_uses_separate_command_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = write_config(Path(temp_dir), codex_enabled=False, opencode_enabled=False, knowledge_enabled=True, write_knowledge_notes=True)
            knowledge_result = type("KnowledgeResult", (), {"enabled": True, "topics": [], "message": "AI knowledge notes generated 0 topic(s)"})()

            with patch("work_journal_agent.cli.generate_knowledge_topics", return_value=knowledge_result) as knowledge, patch(
                "work_journal_agent.cli.write_knowledge_topic_notes", return_value=[]
            ) as writer:
                with redirect_stdout(StringIO()):
                    main(["--config", str(config_path), "generate-knowledge", "--date", "2026-06-12"])

            knowledge.assert_called_once()
            writer.assert_called_once()

    def test_generate_knowledge_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = write_config(Path(temp_dir), codex_enabled=False, opencode_enabled=False)

            with patch("work_journal_agent.cli.generate_knowledge_topics") as knowledge, patch("work_journal_agent.cli.write_knowledge_topic_notes") as writer:
                with redirect_stdout(StringIO()) as stdout:
                    main(["--config", str(config_path), "generate-knowledge", "--date", "2026-06-12"])

            knowledge.assert_not_called()
            writer.assert_not_called()
            self.assertIn("disabled", stdout.getvalue())


def write_config(base: Path, *, codex_enabled: bool, opencode_enabled: bool, knowledge_enabled: bool = False, write_knowledge_notes: bool = False) -> Path:
    config_path = base / "config.toml"
    inbox_path = base / "events.jsonl"
    database_path = base / "work-journal.db"
    output_dir = base / "out"
    config_path.write_text(
        "\n".join(
            [
                "[storage]",
                f'database_path = "{database_path}"',
                f'inbox_path = "{inbox_path}"',
                f'output_dir = "{output_dir}"',
                "",
                "[ai]",
                "enabled = false",
                f"knowledge_enabled = {str(knowledge_enabled).lower()}",
                "",
                "[obsidian]",
                f"write_knowledge_notes = {str(write_knowledge_notes).lower()}",
                "",
                "[sources.codex]",
                f"enabled = {str(codex_enabled).lower()}",
                'sessions_root = "codex"',
                "",
                "[sources.opencode]",
                f"enabled = {str(opencode_enabled).lower()}",
                'storage_root = "opencode"',
                'plugin_path = "opencode-plugin.js"',
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
    return config_path


if __name__ == "__main__":
    unittest.main()
