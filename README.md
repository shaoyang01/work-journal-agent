# work-journal-agent

本地优先的工作日志助手。它把 Codex、OpenCode、Claude Code、Kun、
ZCode 和手动记录里的工作事件，整理成 Obsidian Daily、任务详情和
长期需求资产。

它的目标不是保存完整聊天记录，而是沉淀每天真正有用的信息：

- 原始需求和讨论结论。
- 代码、文档、SQL 等关键产出。
- 待确认问题、风险和后续计划。
- 可复用的需求标题和跨天需求线索。

## 适合谁

- 同时使用多个 AI 编程工具，需要统一沉淀工作记录。
- 希望每天自动生成 Obsidian 工作日报。
- 希望把零散对话整理成可追踪的需求和任务。
- 希望数据保存在本机，不依赖云端工作台。

## 主要功能

- **自动采集**：读取 Codex、OpenCode、Claude Code、Kun、ZCode 等本地
  Agent 事件。
- **AI 整理**：可选 DeepSeek，把事件整理成任务摘要、结论、影响和证据。
- **Obsidian 输出**：生成 `Daily/YYYY-MM-DD.md` 和
  `Tasks/YYYY-MM-DD/需求标题.md`。
- **需求确认**：菜单栏 App 支持改标题、确认、忽略、归并已有需求。
- **历史补生成**：最近 7 天内可以补确认、补生成指定日期日报。
- **本地存储**：事件、确认结果、AI 缓存和状态都写入本机 SQLite。
- **后台同步**：macOS 可通过 launchd 定时导入事件并刷新当天日报。

## 快速开始

内部试用 macOS 版可以直接下载 Release 里的 DMG：

1. 打开 [GitHub Releases](https://github.com/shaoyang01/work-journal-agent/releases)。
2. 下载 `Work-Journal-Agent-版本号.dmg`。
3. 打开 DMG，把 `Work Journal Agent.app` 拖到 Applications。
4. 首次打开如果提示无法验证开发者，在系统设置的隐私与安全性里允许打开。
5. 点击顶部菜单栏 `WJ` 图标，进入设置窗口配置 Obsidian、数据源和 AI。

也可以在源码目录双击：

```text
Install.command
```

或使用命令安装：

```bash
./scripts/install.sh
```

安装后会得到两个等价命令：

```bash
wj --help
work-journal-agent --help
```

## 常用入口

```bash
# 导入当天事件并刷新今天的需求候选
wj sync

# 手动生成某天日报
wj generate-daily --date 2026-06-22

# 记录一条临时事件
wj note "完成历史日报补生成能力"

# 打开配置向导
wj setup
```

macOS 菜单栏 App 可以通过脚本安装或重建：

```bash
scripts/install-menubar.sh
```

顶部状态栏出现 `WJ` 后，可以打开最近 7 天日报/确认、需求管理、设置、
日志和本地数据目录。

## 文档

- [配置指导](docs/configuration.md)：安装、配置文件、数据源、DeepSeek、
  后台同步和卸载。
- [使用指南](docs/usage.md)：日常命令、菜单栏 App、需求确认、历史日报、
  Knowledge、验证和多设备建议。

## 数据与隐私

- 默认数据库在 `~/.local/share/work-journal-agent/work-journal.db`。
- DeepSeek 只接收筛选压缩后的任务事件，不上传完整聊天全文。
- AI 缓存默认保留最近 7 天。
- Obsidian vault 可自行用 iCloud、Git、Obsidian Sync 等方式同步。

## 当前边界

- 目前是本地工具，不提供云端服务。
- DMG 是未公证的内部试用包，正式分发前仍需要 Apple Developer 签名和
  notarization。
- Knowledge 生成功能仍是实验性能力，默认关闭。
