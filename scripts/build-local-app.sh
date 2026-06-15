#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${1:-${ROOT_DIR}/dist/Work Journal Agent.app}"
VERSION="$(python3 - <<'PY' "${ROOT_DIR}/pyproject.toml"
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
print(match.group(1) if match else "0.0.0")
PY
)"
SOURCE_PATH="${ROOT_DIR}/macos/WorkJournalMenuBar/main.swift"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
PLIST_PATH="${CONTENTS_DIR}/Info.plist"
SWIFT_PATH="${MACOS_DIR}/main.swift"
BIN_PATH="${MACOS_DIR}/WorkJournalMenuBar"

if ! command -v swiftc >/dev/null 2>&1; then
  echo "swiftc not found. Please install Xcode Command Line Tools first." >&2
  exit 1
fi

if [[ ! -f "${SOURCE_PATH}" ]]; then
  echo "Missing menu bar source: ${SOURCE_PATH}" >&2
  exit 1
fi

rm -rf "${APP_DIR}"
mkdir -p "${MACOS_DIR}" "${RESOURCES_DIR}"

cat > "${PLIST_PATH}" <<PLIST
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
  <string>${VERSION}</string>
  <key>CFBundleVersion</key>
  <string>${VERSION}</string>
  <key>LSUIElement</key>
  <true/>
</dict>
</plist>
PLIST

cp "${SOURCE_PATH}" "${SWIFT_PATH}"
printf "%s\n" "${ROOT_DIR}" >"${RESOURCES_DIR}/project-root.txt"

swiftc -module-cache-path "${TMPDIR:-/tmp}/work-journal-agent-swift-module-cache" "${SWIFT_PATH}" -o "${BIN_PATH}" -framework AppKit -framework SwiftUI
chmod +x "${BIN_PATH}"

if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "${APP_DIR}" >/dev/null
fi

cat > "${RESOURCES_DIR}/README-local-build.txt" <<EOF
Work Journal Agent local test build

This app is bound to:
${ROOT_DIR}

It calls the Python CLI from that repo with PYTHONPATH=src.
Move the repo and this local test app must be rebuilt.
EOF

echo "Built ${APP_DIR}"
