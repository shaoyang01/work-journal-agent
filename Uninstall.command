#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "work-journal-agent 卸载器"
echo "默认会移除后台任务、Claude Code hooks 和 Python 包安装。"
echo "配置文件和历史事件默认保留。"
echo ""
read -r -p "是否同时删除配置和历史数据？输入 y 删除，直接回车保留: " REMOVE_ALL

if [[ "${REMOVE_ALL}" == "y" || "${REMOVE_ALL}" == "Y" ]]; then
  "${SCRIPT_DIR}/scripts/uninstall.sh" --remove-config --remove-data
else
  "${SCRIPT_DIR}/scripts/uninstall.sh"
fi

echo ""
echo "卸载流程已结束。"
echo ""
read -r -p "按回车关闭窗口..."

