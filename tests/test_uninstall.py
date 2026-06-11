import json
import tempfile
import unittest
from pathlib import Path

from work_journal_agent.setup import remove_claude_hooks


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


if __name__ == "__main__":
    unittest.main()

