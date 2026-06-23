#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(python3 - <<'PY' "${ROOT_DIR}/pyproject.toml"
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
print(match.group(1) if match else "0.0.0")
PY
)"

DIST_DIR="${ROOT_DIR}/dist"
BUILD_DIR="${DIST_DIR}/dmg-build"
VOLUME_DIR="${BUILD_DIR}/volume"
APP_DIR="${VOLUME_DIR}/Work Journal Agent.app"
PROJECT_DIR="${APP_DIR}/Contents/Resources/project"
DMG_PATH="${DIST_DIR}/Work-Journal-Agent-${VERSION}.dmg"
VOL_NAME="Work Journal Agent ${VERSION}"

if ! command -v hdiutil >/dev/null 2>&1; then
  echo "hdiutil not found. DMG builds are only supported on macOS." >&2
  exit 1
fi

"${ROOT_DIR}/scripts/build-local-app.sh" "${APP_DIR}"

rm -rf "${PROJECT_DIR}"
mkdir -p "${PROJECT_DIR}"

rsync -a \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "__pycache__" \
  --exclude ".pytest_cache" \
  --exclude ".mypy_cache" \
  --exclude "dist" \
  --exclude "*.pyc" \
  "${ROOT_DIR}/" "${PROJECT_DIR}/"

printf "@BUNDLE_RESOURCES@/project\n" >"${APP_DIR}/Contents/Resources/project-root.txt"

if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "${APP_DIR}" >/dev/null
fi

ln -sfn /Applications "${VOLUME_DIR}/Applications"
cat >"${VOLUME_DIR}/README-试用说明.txt" <<EOF
Work Journal Agent ${VERSION} 内部试用版

安装方式：
1. 将 "Work Journal Agent.app" 拖到 Applications。
2. 首次打开如果提示无法验证开发者，请在 系统设置 -> 隐私与安全性 中允许打开。
3. 打开后从菜单栏的 WJ 图标进入设置，配置 Obsidian、Agent 数据源和 DeepSeek API Key。

说明：
- 这是未公证的内部试用 DMG，不是正式签名发布包。
- App 内置 work-journal-agent 源码，会通过系统 python3 运行本地 CLI。
- 需要 Python 3.11+；如果 python3 --version 低于 3.11，请先安装或切换到 Python 3.11 以上版本。
EOF

rm -f "${DMG_PATH}"
hdiutil create \
  -volname "${VOL_NAME}" \
  -srcfolder "${VOLUME_DIR}" \
  -ov \
  -format UDZO \
  "${DMG_PATH}"

echo "Built ${DMG_PATH}"
