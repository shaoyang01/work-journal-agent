#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${1:-${ROOT_DIR}/dist/Work Journal Agent.app}"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
PLIST_PATH="${CONTENTS_DIR}/Info.plist"
SWIFT_PATH="${MACOS_DIR}/main.swift"
BIN_PATH="${MACOS_DIR}/WorkJournalMenuBar"
TEMPLATE_PATH="${ROOT_DIR}/scripts/menubar/WorkJournalMenuBar.swift.in"

if ! command -v swiftc >/dev/null 2>&1; then
  echo "swiftc not found. Please install Xcode Command Line Tools first." >&2
  exit 1
fi

rm -rf "${APP_DIR}"
mkdir -p "${MACOS_DIR}" "${RESOURCES_DIR}"

cat > "${PLIST_PATH}" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>WorkJournalMenuBar</string>
  <key>CFBundleIdentifier</key>
  <string>local.work-journal-agent.menubar</string>
  <key>CFBundleName</key>
  <string>Work Journal Agent</string>
  <key>CFBundleDisplayName</key>
  <string>Work Journal Agent</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.6.0-local</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSUIElement</key>
  <true/>
</dict>
</plist>
PLIST

ROOT_DIR="${ROOT_DIR}" TEMPLATE_PATH="${TEMPLATE_PATH}" python3 - <<'PY' > "${SWIFT_PATH}"
import json
import os
from pathlib import Path

root = os.environ["ROOT_DIR"]
template = Path(os.environ["TEMPLATE_PATH"]).read_text(encoding="utf-8")
print(template.replace("__PROJECT_ROOT__", json.dumps(root)))
PY

swiftc "${SWIFT_PATH}" -o "${BIN_PATH}" -framework AppKit
chmod +x "${BIN_PATH}"

cat > "${RESOURCES_DIR}/README-local-build.txt" <<EOF
Work Journal Agent local test build

This app is bound to:
${ROOT_DIR}

It calls the Python CLI from that repo with PYTHONPATH=src.
Move the repo and this local test app must be rebuilt.
EOF

echo "Built ${APP_DIR}"
