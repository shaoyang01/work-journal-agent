import unittest

from datetime import date
import tempfile
from pathlib import Path

from work_journal_agent.config import AppConfig, AiConfig, ClaudeSourceConfig, CodexSourceConfig, KunSourceConfig, MergeConfig, ObsidianConfig, OpenCodeSourceConfig, PrivacyConfig, SourcesConfig, StorageConfig, ZCodeSourceConfig
from work_journal_agent.merge import TaskSummary
from work_journal_agent.writers.obsidian import compact_items, render_daily, render_task, write_daily


class WriterTests(unittest.TestCase):
    def test_compact_items_filters_noise_and_limits_output(self):
        items = [
            "<skill> noisy",
            "第一条有效内容",
            "第二条有效内容",
            "第三条有效内容",
        ]

        self.assertEqual(compact_items(items, limit=2), ["第一条有效内容", "第二条有效内容", "另有 1 条已折叠。"])

    def test_compact_items_truncates_long_text(self):
        result = compact_items(["a" * 20], char_limit=10)

        self.assertEqual(result, ["aaaaaaaaa…"])

    def test_render_daily_uses_overview_and_task_links(self):
        task = TaskSummary(
            key="k1",
            title="排查登录异常",
            day=date(2026, 6, 11),
            cwd="/repo/project-a",
            sources={"codex"},
            raw_requests=["帮我排查登录异常"],
            decisions=["根因是旧 cookie 污染系统标识"],
            files={"/repo/project-a/src/app.py"},
            event_count=3,
        )

        output = render_daily(date(2026, 6, 11), [task])

        self.assertIn("# 工作日报｜2026-06-11", output)
        self.assertIn("## 一、今日概览", output)
        self.assertIn("## 二、需求列表", output)
        self.assertIn("[[Tasks/2026-06-11/排查登录异常\\|排查登录异常]]", output)
        self.assertIn("| 需求事项 | 1 项 |", output)
        self.assertNotIn("- 需求：帮我排查登录异常", output)

    def test_render_daily_shows_requirement_duration_when_available(self):
        task = TaskSummary(
            key="k1",
            title="分拣操作通知数据存储与并发控制方案设计",
            day=date(2026, 6, 13),
            cwd="/repo/wms-out",
            sources={"codex"},
            raw_requests=["继续处理分拣消息"],
            event_count=2,
            requirement_id="req_wms-out-sort",
            requirement_created_at="2026-06-12T09:30:00+08:00",
        )

        output = render_task(task, day=date(2026, 6, 13))

        self.assertIn("| 已进行 | 1 天 |", output)

    def test_render_daily_filters_test_task(self):
        task = TaskSummary(
            key="k1",
            title="测试 work-journal-agent 配置完成",
            day=date(2026, 6, 11),
            cwd="/repo/project-a",
            sources={"manual"},
            event_count=1,
        )

        output = render_daily(date(2026, 6, 11), [task])

        self.assertIn("今天没有可归档的工作事件。", output)

    def test_render_daily_includes_ai_followups_when_present(self):
        task = TaskSummary(
            key="k1",
            title="OpenCode 自动采集",
            day=date(2026, 6, 11),
            cwd="/repo/project-a",
            sources={"codex", "opencode"},
            event_count=4,
            ai_next_actions=["重启 OpenCode 加载插件", "观察 inbox 是否写入"],
            ai_blockers=["缺少真实 OpenCode 启动验证"],
            ai_questions=["是否需要发布安装说明"],
            ai_validation_gaps=["未做端到端插件事件验证"],
            ai_owner_hint="user",
        )

        output = render_daily(date(2026, 6, 11), [task])

        self.assertIn("## 三、风险与阻塞", output)
        self.assertIn("| 阻塞 | 缺少真实 OpenCode 启动验证 | [[Tasks/2026-06-11/opencode-自动采集\\|OpenCode 自动采集]] |", output)
        self.assertIn("| 待确认 | 是否需要发布安装说明 | [[Tasks/2026-06-11/opencode-自动采集\\|OpenCode 自动采集]] |", output)
        self.assertIn("| 验证缺口 | 未做端到端插件事件验证 | [[Tasks/2026-06-11/opencode-自动采集\\|OpenCode 自动采集]] |", output)
        self.assertIn("## 四、明日计划", output)
        self.assertIn("| P0 | 重启 OpenCode 加载插件 | [[Tasks/2026-06-11/opencode-自动采集\\|OpenCode 自动采集]] |", output)
        self.assertIn("| user |", output)

    def test_render_daily_hides_empty_ai_followups(self):
        task = TaskSummary(
            key="k1",
            title="OpenCode 自动采集",
            day=date(2026, 6, 11),
            cwd="/repo/project-a",
            sources={"opencode"},
            event_count=2,
        )

        output = render_daily(date(2026, 6, 11), [task])

        self.assertNotIn("## 三、风险与阻塞", output)
        self.assertNotIn("## 四、明日计划", output)

    def test_render_daily_prefers_important_deliverables(self):
        task = TaskSummary(
            key="k1",
            title="DeepSeek 能力扩展",
            day=date(2026, 6, 12),
            cwd="/repo/work-journal-agent",
            sources={"codex"},
            event_count=5,
            files={"/repo/work-journal-agent/src/work_journal_agent/ai.py"},
            ai_outputs=["修改 ai.py"],
            ai_deliverables=["实现重要产出识别"],
            ai_impact="日报从文件列表升级为成果说明",
            ai_evidence=["python3 -m unittest discover -s tests 通过"],
            ai_artifact_paths=["src/work_journal_agent/ai.py"],
        )

        output = render_task(task, day=date(2026, 6, 12))

        self.assertIn("## 今日产出", output)
        self.assertIn("- 实现重要产出识别", output)
        self.assertIn("## 影响范围", output)
        self.assertIn("日报从文件列表升级为成果说明", output)
        self.assertIn("## 证据依据", output)
        self.assertIn("- python3 -m unittest discover -s tests 通过", output)
        self.assertIn("## 产物路径", output)
        self.assertIn("- src/work_journal_agent/ai.py", output)

    def test_write_daily_always_generates_task_notes_with_escaped_links(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = TaskSummary(
                key="k1",
                title="目视分拣计薪二期",
                day=date(2026, 6, 16),
                cwd="/repo/tms-flink-finance",
                sources={"codex", "zcode"},
                event_count=138,
                ai_questions=["确认方案 C 计薪口径"],
                ai_owner_hint="user",
            )
            config = test_config(base)

            write_daily(config, date(2026, 6, 16), [task])

            daily = (base / "Daily" / "2026-06-16.md").read_text(encoding="utf-8")
            task_path = base / "Tasks" / "2026-06-16" / "目视分拣计薪二期.md"
            self.assertTrue(task_path.exists())
            task_note = task_path.read_text(encoding="utf-8")
            self.assertIn("[[Tasks/2026-06-16/目视分拣计薪二期\\|目视分拣计薪二期]]", daily)
            self.assertIn("[[Daily/2026-06-16\\|2026-06-16 工作日报]]", task_note)

    def test_render_task_prefers_structured_summary_over_raw_events(self):
        task = TaskSummary(
            key="k1",
            title="调拨订单完整流程分析",
            day=date(2026, 6, 17),
            cwd="/repo/wms-out",
            sources={"zcode"},
            raw_requests=[
                "I need to understand the complete allot order flow in the logistics-center project.",
                "This session is being continued from a previous conversation that was compacted.",
            ],
            discussions=[
                "ZCode 用户需求：I need to understand the complete allot order flow",
                "ZCode 执行工具：TodoWrite",
                "ZCode 执行工具：Grep",
            ],
            event_count=52,
            ai_decision="已完成调拨订单流程分析，覆盖调拨生成、集单、分拣回调等关键链路。",
            ai_deliverables=["调拨订单流程调研及关键类/方法定位"],
            ai_impact="为分拣通知落地与后续联调提供业务基础。",
        )

        output = render_task(task, day=date(2026, 6, 17))

        self.assertIn("围绕“调拨订单完整流程分析”开展工作，重点是调拨订单流程调研及关键类/方法定位。", output)
        self.assertIn("- 调拨订单流程调研及关键类/方法定位", output)
        self.assertIn("已完成调拨订单流程分析，覆盖调拨生成、集单、分拣回调等关键链路。", output)
        self.assertNotIn("This session is being continued", output)
        self.assertNotIn("ZCode 执行工具：Grep", output)

    def test_write_daily_does_not_generate_local_knowledge_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = TaskSummary(
                key="k1",
                title="DeepSeek 能力扩展",
                day=date(2026, 6, 12),
                cwd="/repo/work-journal-agent",
                sources={"codex"},
                event_count=5,
                ai_deliverables=["实现知识专题沉淀"],
                ai_impact="把时间线沉淀为专题知识",
                ai_evidence=["53 tests OK"],
                ai_artifact_paths=["src/work_journal_agent/writers/obsidian.py"],
            )
            config = test_config(base)

            write_daily(config, date(2026, 6, 12), [task])
            write_daily(config, date(2026, 6, 12), [task])

            topic = base / "Knowledge" / "work-journal-agent" / "deepseek-能力扩展.md"
            self.assertFalse(topic.exists())

    def test_write_daily_uses_ai_knowledge_topics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            task = TaskSummary(
                key="k1",
                title="知识专题沉淀",
                day=date(2026, 6, 12),
                cwd="/repo/work-journal-agent",
                sources={"codex"},
                event_count=2,
            )
            topics = [
                {
                    "service": "work-journal-agent",
                    "topic": "AI 结果缓存与知识提炼链路",
                    "title": "AI 结果缓存与知识提炼链路",
                    "problem_space": "work-journal-agent 在生成 Daily 后，需要把 DeepSeek 结构化结果转成长期代码库知识，而不是复制任务摘要。",
                    "code_locations": ["src/work_journal_agent/ai.py -> 生成 DeepSeek 知识 Prompt 与缓存 key"],
                    "core_logic": ["Knowledge 正文只写代码库逻辑、约定和判断标准，Daily、证据和产物路径只进入参考索引"],
                    "usage_patterns": ["调整知识输出结构时，要同时升级 Prompt schema、缓存 hash 和 Writer 幂等标记"],
                    "debugging_tips": ["如果正文出现任务完成情况，先检查 code_evidence 是否缺失"],
                    "change_guidelines": ["知识卡片按 service 目录归档，topic 只决定目录下的专题文件"],
                    "pitfalls": ["如果正文出现完成了什么功能，说明知识提炼退化成了日报摘要"],
                    "open_questions": ["后续是否需要读取历史专题后再合并"],
                    "evidence": ["54 tests OK"],
                    "related_tasks": ["知识专题沉淀"],
                    "artifact_paths": ["src/work_journal_agent/ai.py"],
                }
            ]

            write_daily(test_config(base), date(2026, 6, 12), [task], knowledge_topics=topics)

            content = (base / "Knowledge" / "work-journal-agent" / "ai-结果缓存与知识提炼链路.md").read_text(encoding="utf-8")
            self.assertIn("# AI 结果缓存与知识提炼链路", content)
            self.assertIn("### 专题定位", content)
            self.assertIn("work-journal-agent 在生成 Daily 后，需要把 DeepSeek 结构化结果转成长期代码库知识", content)
            self.assertIn("### 代码位置", content)
            self.assertIn("src/work_journal_agent/ai.py -> 生成 DeepSeek 知识 Prompt 与缓存 key", content)
            self.assertIn("### 核心逻辑", content)
            self.assertIn("- Knowledge 正文只写代码库逻辑、约定和判断标准", content)
            self.assertIn("### 使用与修改技巧", content)
            self.assertIn("- 调整知识输出结构时，要同时升级 Prompt schema、缓存 hash 和 Writer 幂等标记", content)
            self.assertIn("### 排障线索", content)
            self.assertIn("如果正文出现任务完成情况", content)
            self.assertIn("### 变更约束", content)
            self.assertIn("知识卡片按 service 目录归档", content)
            self.assertIn("### 常见坑", content)
            self.assertIn("如果正文出现完成了什么功能", content)
            self.assertIn("### 参考索引", content)
            self.assertIn("#### 2026-06-12", content)
            self.assertIn("- Daily：[[Daily/2026-06-12|2026-06-12]]", content)
            self.assertLess(content.index("### 专题定位"), content.index("### 参考索引"))
            self.assertNotIn("wja-knowledge", content)

    def test_write_daily_migrates_old_knowledge_timeline_heading(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            target = base / "Knowledge" / "work-journal-agent" / "ai-结果缓存与知识提炼链路.md"
            target.parent.mkdir(parents=True)
            target.write_text(
                "\n".join(
                    [
                        "---",
                        "type: knowledge-topic",
                        'topic: "work-journal-agent"',
                        "---",
                        "",
                        "# work-journal-agent",
                        "",
                        "## 时间线",
                        "",
                        "<!-- wja-knowledge:2026-06-12:work-journal-agent -->",
                        "### 2026-06-12 - 旧任务摘要",
                        "",
                        "- 摘要：旧格式像日报。",
                        "<!-- /wja-knowledge:2026-06-12:work-journal-agent -->",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            topics = [
                {
                    "service": "work-journal-agent",
                    "topic": "AI 结果缓存与知识提炼链路",
                    "problem_space": "work-journal-agent 的知识输出应围绕代码库理解和 AI 调用链路组织。",
                    "durable_insights": ["Knowledge 不能以时间线作为第一主体"],
                    "evidence": ["用户反馈旧格式与任务摘要无区别"],
                }
            ]

            write_daily(test_config(base), date(2026, 6, 12), [], knowledge_topics=topics)

            content = target.read_text(encoding="utf-8")
            self.assertNotIn("## 时间线", content)
            self.assertIn("### 专题定位", content)
            self.assertIn("work-journal-agent 的知识输出应围绕代码库理解和 AI 调用链路组织。", content)
            self.assertIn("### 参考索引", content)
            self.assertNotIn("旧格式像日报", content)
            self.assertEqual(content.count("#### 2026-06-12"), 1)
            self.assertNotIn("wja-knowledge", content)


def test_config(base: Path) -> AppConfig:
    return AppConfig(
        storage=StorageConfig(inbox_path=base / "events.jsonl", output_dir=base),
        obsidian=ObsidianConfig(
            vault_path=None,
            daily_dir="Daily",
            task_dir="Tasks",
            write_task_notes=False,
            knowledge_dir="Knowledge",
            write_knowledge_notes=True,
        ),
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


if __name__ == "__main__":
    unittest.main()
