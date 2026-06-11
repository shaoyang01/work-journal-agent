import json
import tempfile
import unittest
from pathlib import Path

from work_journal_agent.setup import configure_opencode_plugin, remove_claude_hooks, remove_opencode_plugin


class UninstallTests(unittest.TestCase):
    def test_remove_claude_hooks_only_removes_owned_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Path(temp_dir) / "settings.json"
            project_root = Path("/tmp/work-journal-agent")
            settings.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "UserPromptSubmit": [
                                {"matcher": "", "hooks": [{"type": "command", "command": '"/tmp/work-journal-agent/hooks/claude/hook.sh" UserPromptSubmit'}]},
                                {"matcher": "", "hooks": [{"type": "command", "command": "echo keep"}]},
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            changed = remove_claude_hooks(settings_path=settings, project_root=project_root)

            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertTrue(changed)
            self.assertEqual(len(data["hooks"]["UserPromptSubmit"]), 1)
            self.assertEqual(data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"], "echo keep")

    def test_remove_custom_opencode_plugin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_path = Path(temp_dir) / "custom" / "work-journal-agent.js"
            configure_opencode_plugin(
                plugin_path=plugin_path,
                config_path=Path(temp_dir) / "config.toml",
                project_root=Path("/tmp/work-journal-agent"),
            )

            self.assertTrue(remove_opencode_plugin(plugin_path=plugin_path))
            self.assertFalse(plugin_path.exists())


if __name__ == "__main__":
    unittest.main()
