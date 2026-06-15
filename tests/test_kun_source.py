import json
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
from work_journal_agent.sources.kun import events_from_kun_sources, import_kun_events


class KunSourceTests(unittest.TestCase):
    def test_events_from_kun_sources_extracts_messages_events_and_kunsdd_docs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            storage_root = base / "kun"
            project_root = base / "repo"
            thread_dir = storage_root / "threads" / "thread-1"
            write_json(thread_dir / "thread.json", {"id": "thread-1", "projectRoot": str(project_root)})
            write_jsonl(
                thread_dir / "messages.jsonl",
                [
                    {
                        "id": "msg-1",
                        "role": "user",
                        "content": "帮我实现 Kun 需求采集",
                        "createdAt": "2026-06-15T09:00:00+08:00",
                    },
                    {
                        "id": "msg-2",
                        "role": "assistant",
                        "content": "已完成 Kun importer 和测试。",
                        "createdAt": "2026-06-15T09:05:00+08:00",
                    },
                ],
            )
            write_jsonl(
                thread_dir / "events.jsonl",
                [
                    {
                        "id": "evt-1",
                        "type": "PostToolUse",
                        "tool": "edit",
                        "filePath": str(project_root / "src/app.py"),
                        "timestamp": "2026-06-15T09:06:00+08:00",
                    }
                ],
            )
            requirement_path = project_root / ".kunsdd" / "draft" / "feature-a" / "requirement.md"
            requirement_path.parent.mkdir(parents=True, exist_ok=True)
            requirement_path.write_text("# Kun 采集需求\n\n需要采集 Kun 的需求文档。", encoding="utf-8")
            os.utime(requirement_path, (epoch_seconds("2026-06-15T09:07:00+08:00"), epoch_seconds("2026-06-15T09:07:00+08:00")))

            events, scanned_files = events_from_kun_sources(storage_root, project_root=project_root, config=test_config(base), day=date(2026, 6, 15))

            self.assertEqual(scanned_files, 3)
            self.assertEqual([event.event_type for event in events], ["user_prompt", "conclusion", "file_change", "user_prompt"])
            self.assertEqual(events[0].source, "kun")
            self.assertEqual(events[0].raw_request, "帮我实现 Kun 需求采集")
            self.assertEqual(events[2].files, (str(project_root / "src/app.py"),))
            self.assertEqual(events[3].files, (str(requirement_path),))

    def test_import_kun_events_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            storage_root = base / "kun"
            thread_dir = storage_root / "threads" / "thread-1"
            write_json(thread_dir / "thread.json", {"id": "thread-1"})
            write_jsonl(
                thread_dir / "messages.jsonl",
                [
                    {
                        "id": "msg-1",
                        "role": "user",
                        "content": "记录 Kun 自动采集",
                        "createdAt": "2026-06-15T09:00:00+08:00",
                    }
                ],
            )
            config = test_config(base)

            first = import_kun_events(config, day=date(2026, 6, 15), storage_root=storage_root, project_root=base)
            second = import_kun_events(config, day=date(2026, 6, 15), storage_root=storage_root, project_root=base)

            self.assertEqual(first.imported_events, 1)
            self.assertEqual(second.imported_events, 0)

    def test_events_from_real_kun_metadata_shape_uses_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            storage_root = base / "kun-data"
            project_root = base / "logistics-center"
            thread_dir = storage_root / "threads" / "thr_real"
            write_jsonl(
                thread_dir / "metadata.jsonl",
                [
                    {
                        "kind": "thread_metadata",
                        "thread": {
                            "id": "thr_real",
                            "title": "分析技术方案",
                            "workspace": str(project_root),
                        },
                    }
                ],
            )
            write_jsonl(
                thread_dir / "messages.jsonl",
                [
                    {
                        "id": "item_user",
                        "turnId": "turn_1",
                        "threadId": "thr_real",
                        "role": "user",
                        "kind": "user_message",
                        "text": "分析直送订单出库消息接收方案",
                        "createdAt": "2026-06-15T10:46:44.158Z",
                    },
                    {
                        "id": "item_reasoning",
                        "turnId": "turn_1",
                        "threadId": "thr_real",
                        "role": "assistant",
                        "kind": "assistant_reasoning",
                        "text": "内部推理过程不应该进入日报。",
                        "createdAt": "2026-06-15T10:47:44.158Z",
                    }
                ],
            )

            events, scanned_files = events_from_kun_sources(storage_root, project_root=project_root, config=test_config(base), day=date(2026, 6, 15))

            self.assertEqual(scanned_files, 1)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].cwd, str(project_root))
            self.assertEqual(events[0].raw_request, "分析直送订单出库消息接收方案")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def write_jsonl(path: Path, values: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(value, ensure_ascii=False) for value in values) + "\n", encoding="utf-8")


def epoch_seconds(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


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
            kun=KunSourceConfig(enabled=True, storage_root=base / "kun", project_root=base),
            zcode=ZCodeSourceConfig(enabled=False, storage_root=base / "zcode"),
        ),
    )


if __name__ == "__main__":
    unittest.main()
