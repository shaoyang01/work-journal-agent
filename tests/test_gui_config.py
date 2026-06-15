import os
import tempfile
import unittest
from pathlib import Path

from work_journal_agent.config import load_config
from work_journal_agent.gui_config import build_config_payload, save_config_payload
from work_journal_agent.setup import create_secrets_file


class GuiConfigTests(unittest.TestCase):
    def test_save_config_payload_preserves_ai_model_from_gui_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_config_home = os.environ.get("XDG_CONFIG_HOME")
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_CONFIG_HOME"] = str(base / "config")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config_path = base / "config" / "work-journal-agent" / "config.toml"
                payload = {
                    "config_path": str(config_path),
                    "storage": {
                        "inbox_path": str(base / "events.jsonl"),
                        "output_dir": str(base / "out"),
                    },
                    "obsidian": {
                        "vault_path": str(base / "vault"),
                        "daily_dir": "Daily",
                        "task_dir": "Tasks",
                        "write_task_notes": False,
                        "knowledge_dir": "Knowledge",
                        "write_knowledge_notes": False,
                    },
                    "ai": {
                        "enabled": True,
                        "model": "deepseek-v4-pro",
                        "timeout_seconds": 120,
                        "cache_enabled": True,
                        "cache_retention_days": 7,
                        "cluster_review_enabled": True,
                        "cluster_review_timeout_seconds": 300,
                        "cluster_review_min_confidence": 0.75,
                        "knowledge_enabled": False,
                        "api_key": "test-key",
                    },
                    "sources": {
                        "codex": {
                            "enabled": True,
                            "sessions_root": str(base / "codex"),
                        },
                        "claude": {
                            "enabled": False,
                            "settings_path": str(base / "claude.json"),
                        },
                        "opencode": {
                            "enabled": False,
                            "storage_root": str(base / "opencode-storage"),
                            "plugin_path": str(base / "opencode.js"),
                        },
                        "kun": {
                            "enabled": True,
                            "storage_root": str(base / "kun-storage"),
                            "project_root": str(base / "repo-a"),
                        },
                        "zcode": {
                            "enabled": True,
                            "storage_root": str(base / "zcode-cli"),
                        },
                    },
                }

                saved = save_config_payload(payload, project_root=base, config_path=config_path)
                config = load_config(config_path)

                self.assertEqual(saved["ai"]["model"], "deepseek-v4-pro")
                self.assertEqual(config.ai.model, "deepseek-v4-pro")
                self.assertEqual(config.ai.timeout_seconds, 120)
                self.assertEqual(config.ai.cluster_review_timeout_seconds, 300)
                self.assertTrue(config.sources.kun.enabled)
                self.assertEqual(config.sources.kun.storage_root, base / "kun-storage")
                self.assertEqual(config.sources.kun.project_root, base / "repo-a")
                self.assertTrue(config.sources.zcode.enabled)
                self.assertEqual(config.sources.zcode.storage_root, base / "zcode-cli")
                self.assertTrue((config_path.parent / "secrets.env").exists())
            finally:
                restore_env("XDG_CONFIG_HOME", old_config_home)
                restore_env("XDG_DATA_HOME", old_data_home)

    def test_build_config_payload_reports_existing_api_key_without_exposing_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config_path = base / "config.toml"
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                create_secrets_file(config_path.parent / "secrets.env", "secret")

                payload = build_config_payload(config_path)

                self.assertTrue(payload["ai"]["has_api_key"])
                self.assertNotIn("api_key", payload["ai"])
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)


def restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
