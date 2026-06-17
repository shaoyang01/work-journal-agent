import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

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
from work_journal_agent.event_semantics import enrich_task_semantics, title_is_usable
from work_journal_agent.events import WorkEvent
from work_journal_agent.merge import task_from_events, title_from_summary


class EventSemanticTests(unittest.TestCase):
    def test_skill_invocation_is_stripped_from_local_title_and_semantic_prompt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            raw = (
                "[$speckit-pipeline-confirmed-single](/Users/demo/.codex/skills/speckit-pipeline-confirmed-single/SKILL.md) "
                "遵照 /repo/library/技术文档/目视分拣计薪优化方案.html 要求进行代码修改"
            )
            self.assertNotIn("speckit", title_from_summary(raw).lower())

            task = task_from_events([event("e1", raw, raw_request=raw)])

            def semantic_response(_: object, __: str, prompt: str, **___: object) -> dict[str, object]:
                context = prompt[prompt.index("{") :]
                self.assertNotIn("speckit-pipeline-confirmed-single", context)
                self.assertNotIn("SKILL.md", context)
                return {
                    "title": "目视分拣计薪优化方案代码修改",
                    "summary": "用户要求按目视分拣计薪优化方案修改代码。",
                    "request": "按方案修改目视分拣计薪代码",
                    "evidence": ["用户引用目视分拣计薪优化方案"],
                    "confidence": 0.9,
                }

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}), patch(
                "work_journal_agent.event_semantics.call_deepseek_for_prompt",
                side_effect=semantic_response,
            ):
                result = enrich_task_semantics(test_config(base), [task])

            self.assertTrue(result.used)
            self.assertEqual(task.ai_title, "目视分拣计薪优化方案代码修改")

    def test_semantic_summary_replaces_path_title_with_intent_title(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = task_from_events(
                [
                    event(
                        "e1",
                        "@/Users/demo/.codex/skills/wms-module-production-plan-config-auth/SKILL.md",
                        raw_request="依据这个 skill 初始化生产计划模块配置鉴权版，并同步配置说明",
                        files=["/Users/demo/.codex/skills/wms-module-production-plan-config-auth/SKILL.md"],
                    )
                ]
            )

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}), patch(
                "work_journal_agent.event_semantics.call_deepseek_for_prompt",
                return_value={
                    "title": "初始化生产计划模块配置鉴权版流程",
                    "summary": "用户要求按生产计划配置鉴权版 skill 完成配置初始化。",
                    "request": "初始化生产计划模块配置鉴权版",
                    "outcome": "形成配置说明并同步",
                    "evidence": ["用户引用生产计划配置鉴权版 skill", "请求同步配置说明"],
                    "confidence": 0.88,
                },
            ):
                result = enrich_task_semantics(test_config(base), [task])

            self.assertTrue(result.used)
            self.assertEqual(task.ai_title, "初始化生产计划模块配置鉴权版流程")
            self.assertEqual(task.ai_request, "初始化生产计划模块配置鉴权版")
            self.assertIn("用户引用生产计划配置鉴权版 skill", task.ai_evidence)

    def test_semantic_summary_rejects_path_like_ai_title(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = task_from_events([event("e1", "@/repo/service/src/main/java/demo/App.java", raw_request="分析这个文件")])

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}), patch(
                "work_journal_agent.event_semantics.call_deepseek_for_prompt",
                return_value={
                    "title": "/Users/demo/repo/service/src/main/java/demo/App.java",
                    "summary": "用户要求分析指定 Java 文件。",
                    "request": "分析指定 Java 文件",
                    "evidence": ["用户输入了 Java 文件路径"],
                    "confidence": 0.91,
                },
            ):
                result = enrich_task_semantics(test_config(base), [task])

            self.assertTrue(result.used)
            self.assertIsNone(task.ai_title)
            self.assertEqual(task.ai_request, "分析指定 Java 文件")
            self.assertFalse(title_is_usable("/Users/demo/repo/service/src/main/java/demo/App.java"))

    def test_recursive_semantic_summary_uses_cache_for_segments_and_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = task_from_events(
                [
                    event(f"e{i}", f"继续分析生产计划分拣消息链路 {i}", raw_request=f"看一下 EventCenter 发送方式 {i}")
                    for i in range(50)
                ]
            )

            def semantic_response(_: object, __: str, prompt: str, **___: object) -> dict[str, object]:
                context = json.loads(prompt[prompt.index("{") :])
                if "child_summaries" in context:
                    return {
                        "title": "梳理生产计划分拣消息接收链路",
                        "summary": "跨片段梳理消息接收入口和 EventCenter 发送方式。",
                        "request": "梳理生产计划分拣消息链路",
                        "outcome": "明确消息接收和发送方式分析范围",
                        "evidence": ["片段一", "片段二"],
                        "confidence": 0.9,
                    }
                if context["segment_index"] == 0:
                    return {
                        "title": "分析分拣消息接收片段一",
                        "summary": "片段一围绕消息接收入口。",
                        "request": "分析消息接收入口",
                        "evidence": ["前 40 条事件"],
                        "confidence": 0.8,
                    }
                return {
                    "title": "分析分拣消息接收片段二",
                    "summary": "片段二补充 EventCenter 发送方式。",
                    "request": "补充 EventCenter 发送方式",
                    "evidence": ["后 10 条事件"],
                    "confidence": 0.81,
                }

            config = test_config(base)
            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}), patch(
                "work_journal_agent.event_semantics.call_deepseek_for_prompt",
                side_effect=semantic_response,
            ) as call:
                first = enrich_task_semantics(config, [task])
                second = enrich_task_semantics(config, [task])

            self.assertTrue(first.used)
            self.assertTrue(second.used)
            self.assertEqual(call.call_count, 3)
            self.assertEqual(task.ai_title, "梳理生产计划分拣消息接收链路")


def test_config(base: Path) -> AppConfig:
    return AppConfig(
        storage=StorageConfig(inbox_path=base / "events.jsonl", output_dir=base / "out"),
        obsidian=ObsidianConfig(
            vault_path=None,
            daily_dir="Daily",
            task_dir="Tasks",
            write_task_notes=False,
            knowledge_dir="Knowledge",
            write_knowledge_notes=False,
        ),
        privacy=PrivacyConfig(max_raw_request_chars=500, store_transcript_paths=True),
        merge=MergeConfig(min_keyword_overlap=1),
        ai=AiConfig(
            enabled=True,
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


def event(event_id: str, summary: str, *, raw_request: str | None = None, files: list[str] | None = None) -> WorkEvent:
    return WorkEvent(
        id=event_id,
        source="codex",
        event_type="user_prompt",
        occurred_at=datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc),
        cwd="/repo/wms-out",
        summary=summary,
        raw_request=raw_request,
        decision=None,
        files=tuple(files or ()),
        metadata={"session_id": "s1"},
    )


if __name__ == "__main__":
    unittest.main()
