import unittest

from work_journal_agent.config import strip_wrapping_quotes
from pathlib import Path

from work_journal_agent.setup import (
    configure_opencode_plugin,
    default_opencode_plugin_path,
    opencode_plugin_content,
    remove_opencode_plugin,
    upsert_ai_config,
)


class ConfigTests(unittest.TestCase):
    def test_strip_wrapping_quotes_handles_terminal_paste(self):
        self.assertEqual(
            strip_wrapping_quotes("'/Users/demo/Documents/My Vault'"),
            "/Users/demo/Documents/My Vault",
        )

    def test_strip_wrapping_quotes_handles_nested_quotes(self):
        self.assertEqual(
            strip_wrapping_quotes("\"'/tmp/demo vault'\""),
            "/tmp/demo vault",
        )

    def test_upsert_ai_config_appends_block(self):
        text = "[storage]\noutput_dir = \"out\"\n"

        result = upsert_ai_config(text, enabled=True)

        self.assertIn("[ai]", result)
        self.assertIn("enabled = true", result)

    def test_upsert_ai_config_replaces_existing_block(self):
        text = "[ai]\nenabled = false\nmodel = \"old\"\n\n[merge]\nmin_keyword_overlap = 1\n"

        result = upsert_ai_config(text, enabled=True)

        self.assertIn("enabled = true", result)
        self.assertIn('model = "deepseek-v4-flash"', result)
        self.assertNotIn('model = "old"', result)
        self.assertIn("[merge]", result)

    def test_opencode_plugin_content_forwards_tracked_events(self):
        content = opencode_plugin_content(config_path=Path("/tmp/wja/config.toml"), project_root=Path("/tmp/work-journal-agent"))

        self.assertIn("work-journal-agent managed OpenCode plugin", content)
        self.assertIn('"message.updated"', content)
        self.assertIn('"tool.execute.after"', content)
        self.assertIn('"--config", CONFIG_PATH, "opencode", "hook"', content)
        self.assertIn('PYTHONPATH = "/tmp/work-journal-agent/src"', content)

    def test_configure_and_remove_opencode_plugin_are_idempotent(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_path = Path(temp_dir) / "opencode" / "plugins" / "work-journal-agent.js"
            configure_opencode_plugin(
                plugin_path=plugin_path,
                config_path=Path(temp_dir) / "config.toml",
                project_root=Path("/tmp/work-journal-agent"),
            )

            self.assertTrue(plugin_path.exists())
            self.assertTrue(remove_opencode_plugin(plugin_path=plugin_path))
            self.assertFalse(plugin_path.exists())
            self.assertFalse(remove_opencode_plugin(plugin_path=plugin_path))

    def test_remove_opencode_plugin_keeps_user_plugin(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_path = Path(temp_dir) / "opencode" / "plugins" / "custom.js"
            plugin_path.parent.mkdir(parents=True)
            plugin_path.write_text("export const Custom = async () => ({})\n", encoding="utf-8")

            self.assertFalse(remove_opencode_plugin(plugin_path=plugin_path))
            self.assertTrue(plugin_path.exists())


if __name__ == "__main__":
    unittest.main()
