import os
import tempfile
import unittest
from pathlib import Path

from work_journal_agent.scheduler import active_window_command, daily_command, install_interval_schedule, parse_time


class SchedulerTests(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(parse_time("23:30"), (23, 30))

    def test_parse_time_rejects_invalid_value(self):
        with self.assertRaises(ValueError):
            parse_time("25:00")

    def test_daily_command_quotes_project_root(self):
        command = daily_command(Path("/tmp/demo project"))
        self.assertIn("cd '/tmp/demo project'", command)
        self.assertIn("work_journal_agent sync", command)
        self.assertIn("secrets.env", command)

    def test_daily_command_can_limit_active_window(self):
        command = daily_command(Path("/tmp/demo project"), active_from="09:30", active_to="18:00")

        self.assertIn("start=0930", command)
        self.assertIn("end=1800", command)
        self.assertIn("date +%H%M", command)

    def test_install_interval_schedule_defaults_to_workday_window(self):
        with tempfile.TemporaryDirectory() as temp_home:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = temp_home
            try:
                result = install_interval_schedule(
                    project_root=Path("/tmp/demo project"),
                    every_minutes=60,
                    load=False,
                )
                content = result.path.read_text(encoding="utf-8")

                self.assertIn("start=0800", content)
                self.assertIn("end=2100", content)
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home

    def test_active_window_requires_both_bounds(self):
        with self.assertRaises(ValueError):
            active_window_command("09:00", None)

    def test_install_interval_schedule_writes_plist_without_loading(self):
        with tempfile.TemporaryDirectory() as temp_home:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = temp_home
            try:
                result = install_interval_schedule(
                    project_root=Path("/tmp/demo project"),
                    every_minutes=60,
                    active_from="09:00",
                    active_to="20:00",
                    load=False,
                )
                self.assertTrue(result.path.exists())
                content = result.path.read_text(encoding="utf-8")
                self.assertIn("<key>StartInterval</key>", content)
                self.assertIn("<integer>3600</integer>", content)
                self.assertIn("start=0900", content)
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
