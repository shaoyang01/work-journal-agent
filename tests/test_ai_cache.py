import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from work_journal_agent.ai_cache import (
    apply_cached_result,
    delta_context,
    find_cache_match,
    load_cache,
    prune_cache,
    save_cache,
    task_cache_entry,
)
from work_journal_agent.merge import TaskSummary


class AiCacheTests(unittest.TestCase):
    def test_find_cache_match_by_event_ids(self):
        task = TaskSummary(key="k1", title="任务", day=date(2026, 6, 12), cwd="/repo/a", event_ids={"e2"})
        entries = [
            {"repo": "a", "event_ids": ["e1"], "ai_result": {"title": "旧"}},
            {"repo": "a", "event_ids": ["e2"], "ai_result": {"title": "新"}},
        ]

        match = find_cache_match(task, entries)

        self.assertIsNotNone(match)
        self.assertEqual(match.ai_results[0]["title"], "新")

    def test_delta_context_keeps_only_new_values(self):
        current = {
            "original_request": "原始需求",
            "additional_requests": ["追加 A", "追加 B"],
            "latest_decisions": ["旧结论", "新结论"],
            "process_evidence": ["旧过程", "新过程"],
            "files": ["old.py", "new.py"],
        }
        previous = [
            {
                "original_request": "原始需求",
                "additional_requests": ["追加 A"],
                "latest_decisions": ["旧结论"],
                "process_evidence": ["旧过程"],
                "files": ["old.py"],
            }
        ]

        delta = delta_context(current, previous)

        self.assertEqual(delta["new_requests"], ["追加 B"])
        self.assertEqual(delta["new_decisions"], ["新结论"])
        self.assertEqual(delta["new_process_evidence"], ["新过程"])
        self.assertEqual(delta["new_files"], ["new.py"])

    def test_save_load_and_apply_cached_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            task = TaskSummary(key="k1", title="任务", day=date(2026, 6, 12), cwd="/repo/a", event_ids={"e1"})
            entry = task_cache_entry(
                task,
                context={"key": "k1", "files": []},
                ai_result={
                    "title": "AI 标题",
                    "outputs": ["产出"],
                    "deliverables": ["重要产出"],
                    "impact": "提升日报可读性",
                    "evidence": ["49 tests OK"],
                    "artifact_paths": ["src/work_journal_agent/ai.py"],
                    "next_actions": ["下一步"],
                    "owner_hint": "agent",
                },
            )

            save_cache(cache_dir, date(2026, 6, 12), [entry])
            loaded = load_cache(cache_dir, date(2026, 6, 12))
            apply_cached_result(task, loaded["tasks"][0]["ai_result"])

            self.assertEqual(task.ai_title, "AI 标题")
            self.assertEqual(task.ai_outputs, ["产出"])
            self.assertEqual(task.ai_deliverables, ["重要产出"])
            self.assertEqual(task.ai_impact, "提升日报可读性")
            self.assertEqual(task.ai_evidence, ["49 tests OK"])
            self.assertEqual(task.ai_artifact_paths, ["src/work_journal_agent/ai.py"])
            self.assertEqual(task.ai_next_actions, ["下一步"])
            self.assertEqual(task.ai_owner_hint, "agent")

    def test_prune_cache_keeps_recent_days(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            for day in ("2026-06-05", "2026-06-10", "2026-06-12"):
                (cache_dir / f"{day}.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")

            prune_cache(cache_dir, keep_days=3, today=date(2026, 6, 12))

            self.assertFalse((cache_dir / "2026-06-05.json").exists())
            self.assertTrue((cache_dir / "2026-06-10.json").exists())
            self.assertTrue((cache_dir / "2026-06-12.json").exists())


if __name__ == "__main__":
    unittest.main()
