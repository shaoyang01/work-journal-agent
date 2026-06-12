# work-journal-agent

本地优先的工作日志代理，用来把 Codex、OpenCode、Claude Code、手动标记和 Git 证据整理成 Obsidian 工作笔记。

它的目标不是保存完整聊天记录，而是沉淀任务资产：原始需求、讨论方案、最终结论、产出和后续。

## 适合谁

- 同时使用 Codex、OpenCode、Claude Code 或其他本地 Agent 工具。
- 希望每天自动生成 Obsidian 工作记录。
- 希望在多台个人设备上安装使用，同时也能分享给别人。

## 安装

macOS 最简单方式：在 Finder 里双击：

```text
Install.command
```

它会自动安装工具，并用中文询问 Obsidian 路径、是否启用 DeepSeek、是否配置 Claude Code hooks、是否安装后台自动写入器。

源码目录内也可以运行启动脚本：

macOS/Linux：

```bash
./scripts/install.sh
```

Windows PowerShell：

```powershell
.\scripts\start.ps1
```

如果想接受默认值快速初始化：

```bash
./scripts/install.sh --yes
```

开发安装：

```bash
python -m pip install -e .
```

如果你使用 `uv`：

```bash
uv tool install .
```

安装后会得到两个等价命令：

```bash
wj --help
work-journal-agent --help
```

## 配置

安装后也可以直接运行配置向导：

```bash
wj setup
```

配置向导会询问：

- 配置文件路径。
- inbox JSONL 路径。
- Obsidian vault 路径。
- Daily / Tasks 目录名。
- 是否写独立任务笔记。
- Knowledge 目录名。
- 是否生成或更新知识专题笔记。
- 是否启用 DeepSeek AI 分析；启用时会继续询问 API Key，并保存到本机私有 `secrets.env`。
- 如果不启用 DeepSeek，则使用本地规则摘要。
- 是否启用 Codex 采集；启用后会询问 sessions 根目录。
- 是否启用 Claude Code 采集 hooks；启用后才会询问 settings.json 路径。
- 是否启用 OpenCode 采集插件；启用后才会询问插件保存路径。

手动配置方式如下。

复制配置模板：

```bash
mkdir -p ~/.config/work-journal-agent
cp config/config.example.toml ~/.config/work-journal-agent/config.toml
```

Windows 可以放到：

```text
%APPDATA%\work-journal-agent\config.toml
```

最小配置：

```toml
[storage]
inbox_path = "~/.local/share/work-journal-agent/inbox/events.jsonl"
output_dir = "./out"

[obsidian]
vault_path = "/path/to/your/ObsidianVault"
daily_dir = "Daily"
task_dir = "Tasks"
write_task_notes = false
```

`vault_path` 留空时，会写到 `storage.output_dir`，方便先试跑。

## 手动记录事件

日常临时记一条，优先用短命令：

```bash
wj note "今天完成了 work-journal-agent 的自动采集和后台写入"
```

更完整的事件命令仍然保留：

```bash
wj event add \
  --source codex \
  --type conclusion \
  --summary "确定采用 Claude hooks + JSONL inbox 方案" \
  --decision "第一版先做本地 CLI，不做后台服务"
```

记录文件产出：

```bash
wj event add \
  --source manual \
  --type note \
  --summary "补充 README 安装说明" \
  --file README.md
```

## 生成每日笔记

正常情况下不需要手动运行，macOS 会通过 `launchd` 后台定期执行：

```bash
wj sync
```

它会先导入 Codex 当天 session 和 OpenCode 当天事件，再生成今天的 Obsidian Daily。Knowledge 不会在 `wj sync` 中生成，避免日报和知识沉淀共用一次长时间等待。

手动预览：

```bash
wj sync --date 2026-06-11 --dry-run
```

写入 Obsidian 或 fallback 输出目录：

```bash
wj sync --date 2026-06-11
```

实验性代码库知识生成默认关闭。只有同时开启 `[ai].knowledge_enabled` 和 `[obsidian].write_knowledge_notes` 后，才会单独生成 Knowledge：

```bash
wj generate-knowledge --date 2026-06-11
```

当前建议保持关闭，等知识沉淀策略明确后再启用。

## 需求确认与菜单栏 App

如果 Daily 里出现文件路径式标题，或同一个需求跨 Claude/Codex 多轮方案、实现、review，可以用 macOS 顶部菜单栏 App 直接弹出原生确认窗口：

```bash
scripts/install-menubar.sh
```

安装后顶部状态栏会出现 `WJ`。点击菜单可以：

- 打开今日需求确认窗口
- 同步最新事件
- 生成今日日报
- 打开设置窗口
- 打开本地数据目录和日志

如果只想生成一个可双击的本机测试版，不安装到 `~/Applications`，可以构建到 `dist/`：

```bash
scripts/build-local-app.sh
```

然后在 Finder 里双击：

```text
dist/Work Journal Agent.app
```

这个本机测试版会绑定当前仓库路径，并调用当前仓库下的 Python CLI。移动仓库后需要重新构建。

菜单栏 App 不启动本地 HTTP 服务，也不打开浏览器。它通过 CLI JSON 通道调用本地核心逻辑：

```bash
wj requirements payload --date 2026-06-12
wj requirements save --date 2026-06-12
wj app config
wj app config-save
wj app status
```

确认窗口会列出当天候选需求，支持改标题、确认、标记待确认或忽略。保存后会写入本机：

```text
~/.local/share/work-journal-agent/requirements/threads.json
~/.local/share/work-journal-agent/requirements/daily/YYYY-MM-DD.json
~/.local/share/work-journal-agent/state/status.json
```

后续 `wj generate-daily` 会优先使用已确认的需求标题。

本地 HTML 确认页仍保留为调试入口，需要时可手动启动：

```bash
wj requirements review --date 2026-06-12
```

## OpenCode 采集

安装向导默认会询问是否配置 OpenCode 采集插件。启用后会生成：

```text
~/.config/opencode/plugins/work-journal-agent.js
```

OpenCode 启动时会自动加载这个插件，插件会把消息、工具执行、文件变更、session diff 等事件写入 work-journal-agent。重复运行 `wj setup` 会刷新这个插件文件。

导入当天 OpenCode 本地事件：

```bash
wj opencode import --date 2026-06-11
```

默认读取：

```text
~/.local/share/opencode/storage
```

也可以指定路径：

```bash
wj opencode import --storage-root /path/to/opencode/storage
```

如果你写 OpenCode 插件，可以把插件事件 JSON 通过 stdin 交给：

```bash
wj opencode hook
```

采集器只保存工作日志需要的摘要、文件列表、工具名和会话标识；不会把 OpenCode diff 的 before/after 全量内容写入 inbox。

卸载时会删除由 work-journal-agent 生成的 OpenCode 插件：

```bash
wj uninstall
```

如果安装时用了自定义插件路径，卸载时可以指定：

```bash
wj uninstall --opencode-plugin /path/to/work-journal-agent.js
```

## 后台自动写入

macOS 下配置向导会询问是否安装后台自动写入器。开启后会创建：

```text
~/Library/LaunchAgents/com.shaoyang01.work-journal-agent.daily.plist
```

默认每 60 分钟刷新一次今天的 Daily，电脑重启并登录后会自动恢复。
如果启用了 DeepSeek，后台任务会自动加载：

```text
~/.config/work-journal-agent/secrets.env
```

这个文件权限会设置为 `600`，不要提交到 Git。

## DeepSeek AI 分析

安装向导会询问是否启用 DeepSeek AI 分析：

```text
是否启用 DeepSeek AI 分析，让它帮助整理每日工作摘要
```

如果选择启用，可以直接输入 API Key。它会写入本机私有文件：

```text
~/.config/work-journal-agent/secrets.env
```

如果机器上已经有 `DEEPSEEK_API_KEY` 环境变量，也可以在安装器询问 API Key 时直接回车使用现有环境变量。

```bash
export DEEPSEEK_API_KEY="你的 key"
```

配置文件中对应开关：

```toml
[ai]
enabled = true
provider = "deepseek"
base_url = "https://api.deepseek.com"
model = "deepseek-v4-flash"
api_key_env = "DEEPSEEK_API_KEY"
timeout_seconds = 120
cache_enabled = true
cache_retention_days = 7
cluster_review_enabled = true
cluster_review_min_confidence = 0.75
knowledge_enabled = false
```

DeepSeek 只会收到已经筛选压缩过的任务事件，不会上传完整 Codex/Claude/OpenCode 聊天全文。AI 结果默认按天缓存在 `~/.local/share/work-journal-agent/ai-cache/YYYY-MM-DD.json`，保留最近 7 天；如果任务没有新增事件，会复用缓存而不重复调用 DeepSeek。

启用 `cluster_review_enabled` 后，生成 Daily 前会先让 DeepSeek 审查规则聚类结果。高置信度建议会自动合并同一任务或拆分误合并任务；低置信度、返回异常或调用失败时保持本地规则聚类结果，日报仍会正常生成。

DeepSeek 摘要还会识别重要产出：把“修改了哪些文件”提升为“真正完成了什么”，并在 Daily 中展示影响、验证证据和关键产物路径。旧缓存或旧模型只返回 `outputs` 时，会自动按旧字段回退展示。

Knowledge 生成功能目前是实验性能力，默认关闭。只有开启 `[ai].knowledge_enabled = true` 且 `[obsidian].write_knowledge_notes = true` 时，`wj generate-knowledge` 才会调用 DeepSeek；关闭时不会调用 DeepSeek，也不会执行本地 Knowledge 兜底写入。

已经安装过之后，也可以只配置 DeepSeek：

```bash
wj ai setup
```

它会询问 API Key，并自动完成：

- 写入 `~/.config/work-journal-agent/secrets.env`
- 开启配置文件里的 `[ai].enabled`
- 重新安装后台自动同步任务

关闭 DeepSeek 辅助总结：

```bash
wj ai disable
```

手动安装或重装：

```bash
wj schedule install --every-minutes 60
```

查看状态：

```bash
wj schedule status
```

卸载后台任务：

```bash
wj schedule uninstall
```

## Codex 自动采集

Codex 当前通过本地 session 日志导入，不需要 hook：

```text
~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
```

后台 `wj sync` 会自动导入当天新增的 Codex 用户需求、最终回复和 patch 文件事件。导入使用稳定 key 去重，重复运行不会重复写入 inbox。

## 本地验证

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

## Claude Code Hooks

安装 CLI 后，可以在 Claude Code settings 中配置 hook。

macOS/Linux 示例：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/work-journal-agent/hooks/claude/hook.sh UserPromptSubmit"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/work-journal-agent/hooks/claude/hook.sh PostToolUse"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/work-journal-agent/hooks/claude/hook.sh Stop"
          }
        ]
      }
    ]
  }
}
```

Windows PowerShell 示例：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "powershell -ExecutionPolicy Bypass -File C:\\path\\to\\work-journal-agent\\hooks\\claude\\hook.ps1 UserPromptSubmit"
          }
        ]
      }
    ]
  }
}
```

hook 只写轻量事件，不会把 transcript 全量写入 Obsidian。

## 卸载

macOS 最简单方式：在 Finder 里双击：

```text
Uninstall.command
```

它会询问是否同时删除配置和历史数据。

macOS/Linux：

```bash
./scripts/uninstall.sh
```

Windows PowerShell：

```powershell
.\scripts\uninstall.ps1
```

默认卸载会移除：

- launchd 后台任务。
- Claude Code settings 里的 work-journal-agent hooks。
- Python 包安装。

默认保留配置和历史数据。需要一起删除时：

```bash
./scripts/uninstall.sh --remove-config --remove-data
```

## 多设备使用建议

- 项目代码用 Git 同步。
- 每台设备维护自己的 `~/.config/work-journal-agent/config.toml`。
- Obsidian vault 可以用 iCloud、Syncthing、Git 或 Obsidian Sync 同步。
- inbox 默认在本机用户目录，不建议提交到仓库。

## 当前边界

第一版使用确定性规则归并任务：

- 同一天。
- 同 repo/cwd。
- 文件重叠或关键词重叠。

暂不做云端服务、Notion/飞书输出和 GUI。
