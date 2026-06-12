import tempfile
import unittest
from pathlib import Path

from work_journal_agent.config import load_config, strip_wrapping_quotes
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
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_path = Path(temp_dir) / "opencode" / "plugins" / "custom.js"
            plugin_path.parent.mkdir(parents=True)
            plugin_path.write_text("export const Custom = async () => ({})\n", encoding="utf-8")

            self.assertFalse(remove_opencode_plugin(plugin_path=plugin_path))
            self.assertTrue(plugin_path.exists())

    def test_load_config_reads_sources_and_ai_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        "[ai]",
                        "enabled = true",
                        "cache_enabled = true",
                        "cache_retention_days = 3",
                        'cache_dir = "cache"',
                        "cluster_review_enabled = false",
                        "cluster_review_min_confidence = 0.8",
                        "",
                        "[sources.codex]",
                        "enabled = false",
                        'sessions_root = "codex-sessions"',
                        "",
                        "[sources.claude]",
                        "enabled = true",
                        'settings_path = "claude/settings.json"',
                        "",
                        "[sources.opencode]",
                        "enabled = false",
                        'storage_root = "opencode-storage"',
                        'plugin_path = "opencode/plugin.js"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertTrue(config.ai.cache_enabled)
            self.assertEqual(config.ai.cache_retention_days, 3)
            self.assertEqual(config.ai.cache_dir, Path(temp_dir) / "cache")
            self.assertFalse(config.ai.cluster_review_enabled)
            self.assertEqual(config.ai.cluster_review_min_confidence, 0.8)
            self.assertFalse(config.ai.knowledge_enabled)
            self.assertEqual(config.obsidian.knowledge_dir, "Knowledge")
            self.assertFalse(config.obsidian.write_knowledge_notes)
            self.assertFalse(config.sources.codex.enabled)
            self.assertEqual(config.sources.codex.sessions_root, Path(temp_dir) / "codex-sessions")
            self.assertTrue(config.sources.claude.enabled)
            self.assertFalse(config.sources.opencode.enabled)


if __name__ == "__main__":
    unittest.main()
