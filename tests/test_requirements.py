import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from work_journal_agent.config import (
    AiConfig,
    AppConfig,
    ClaudeSourceConfig,
    CodexSourceConfig,
    KunSourceConfig,
    MergeConfig,
    ObsidianConfig,
    OpenCodeSourceConfig,
    PrivacyConfig,
    SourcesConfig,
    StorageConfig,
    ZCodeSourceConfig,
)
from work_journal_agent.events import WorkEvent, append_event
from work_journal_agent.merge import group_events
from work_journal_agent.requirements import apply_requirement_assignments, build_review_payload, save_review_decisions


class RequirementReviewTests(unittest.TestCase):
    def test_build_review_payload_marks_path_title_low_confidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = test_config(base)
                append_event(
                    config.storage.inbox_path,
                    event(
                        "e1",
                        "@/repo/service/src/main/java/demo/App.java",
                        raw_request="@/repo/service/src/main/java/demo/App.java",
                        cwd="/repo/service",
                    ),
                )

                payload = build_review_payload(config, date(2026, 6, 12))

                self.assertEqual(payload["summary"]["total_candidates"], 1)
                candidate = payload["candidates"][0]
                self.assertLess(candidate["confidence"], 0.85)
                self.assertIn("建议人工改名", " ".join(candidate["reasons"]))
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)

    def test_confirmed_requirement_title_is_applied_to_daily_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_data_home = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(base / "data")
            try:
                config = test_config(base)
                work_event = event("e1", "Codex 用户需求：按方案实现", raw_request="按方案实现视觉分拣 ETL 改造", cwd="/repo/tms-flink-finance")
                append_event(config.storage.inbox_path, work_event)
                tasks = group_events([work_event], min_keyword_overlap=1)
                candidate_id = "cand_test"

                save_review_decisions(
                    date(2026, 6, 12),
                    [
                        {
                            "candidate_id": candidate_id,
                            "title": "视觉分拣 ETL 生产波次绑定关系历史查询改造",
                            "project": "tms-flink-finance",
                            "requirement_type": "plan-driven",
                            "status": "confirmed",
                            "event_ids": [work_event.id],
                            "anchors": {"implementation_files": ["EmployeeSupportWorkVisualSortingEtl.java"]},
                        }
                    ],
                    config=config,
                )

                apply_requirement_assignments(config, date(2026, 6, 12), tasks)

                self.assertEqual(tasks[0].ai_title, "视觉分拣 ETL 生产波次绑定关系历史查询改造")
            finally:
                restore_env("XDG_DATA_HOME", old_data_home)


def test_config(base: Path) -> AppConfig:
    return AppConfig(
        storage=StorageConfig(inbox_path=base / "events.jsonl", output_dir=base / "out"),
        obsidian=ObsidianConfig(vault_path=None, daily_dir="Daily", task_dir="Tasks", write_task_notes=False, knowledge_dir="Knowledge", write_knowledge_notes=False),
        privacy=PrivacyConfig(max_raw_request_chars=500, store_transcript_paths=True),
        merge=MergeConfig(min_keyword_overlap=1),
        ai=AiConfig(
            enabled=False,
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key_env="DEEPSEEK_API_KEY",
            timeout_seconds=120,
            cache_enabled=True,
            cache_retention_days=7,
            cache_dir=base / "ai-cache",
            cluster_review_enabled=True,
            cluster_review_timeout_seconds=240,
            cluster_review_min_confidence=0.75,
            knowledge_enabled=False,
        ),
        sources=SourcesConfig(
            codex=CodexSourceConfig(enabled=False, sessions_root=base / "codex"),
            claude=ClaudeSourceConfig(enabled=False, settings_path=base / "claude.json"),
            opencode=OpenCodeSourceConfig(enabled=False, storage_root=base / "opencode", plugin_path=base / "plugin.js"),
            kun=KunSourceConfig(enabled=False, storage_root=base / "kun", project_root=base),
            zcode=ZCodeSourceConfig(enabled=False, storage_root=base / "zcode"),
        ),
    )


def event(event_id: str, summary: str, *, raw_request: str | None = None, cwd: str) -> WorkEvent:
    return WorkEvent(
        id=event_id,
        source="codex",
        event_type="user_prompt",
        occurred_at=datetime.fromisoformat("2026-06-12T09:00:00+08:00"),
        cwd=cwd,
        summary=summary,
        raw_request=raw_request,
        decision=None,
        files=(),
        metadata={},
    )


def restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
