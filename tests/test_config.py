import unittest

from work_journal_agent.config import strip_wrapping_quotes
from work_journal_agent.setup import upsert_ai_config


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


if __name__ == "__main__":
    unittest.main()
