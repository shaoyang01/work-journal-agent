import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

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


def write_config(base: Path, *, codex_enabled: bool, opencode_enabled: bool) -> Path:
    config_path = base / "config.toml"
    inbox_path = base / "events.jsonl"
    output_dir = base / "out"
    config_path.write_text(
        "\n".join(
            [
                "[storage]",
                f'inbox_path = "{inbox_path}"',
                f'output_dir = "{output_dir}"',
                "",
                "[ai]",
                "enabled = false",
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
            ]
        ),
        encoding="utf-8",
    )
    return config_path


if __name__ == "__main__":
    unittest.main()
