#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "work-journal-agent 安装器"
echo "接下来会自动安装命令行工具，并进入中文配置向导。"
echo ""

"${SCRIPT_DIR}/scripts/install.sh"

echo ""
echo "安装流程已结束。"
echo "如果你启用了后台自动写入器，它会在登录后自动恢复，并定期同步到 Obsidian。"
echo ""
read -r -p "按回车关闭窗口..."

