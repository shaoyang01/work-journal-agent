#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${HOME}/Applications/Work Journal Agent.app"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
PLIST_PATH="${CONTENTS_DIR}/Info.plist"
SWIFT_PATH="${MACOS_DIR}/main.swift"
BIN_PATH="${MACOS_DIR}/WorkJournalMenuBar"

if ! command -v swiftc >/dev/null 2>&1; then
  echo "swiftc not found. Please install Xcode Command Line Tools first." >&2
  exit 1
fi

mkdir -p "${MACOS_DIR}"

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
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSUIElement</key>
  <true/>
</dict>
</plist>
PLIST

ROOT_DIR="${ROOT_DIR}" python3 - <<'PY' > "${SWIFT_PATH}"
import json
import os

root = os.environ["ROOT_DIR"]
template = r'''
import AppKit

let projectRoot = __PROJECT_ROOT__

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "WJ"
        rebuildMenu()
    }

    private func rebuildMenu() {
        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "Work Journal", action: nil, keyEquivalent: ""))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(item("同步最新事件", #selector(syncNow)))
        menu.addItem(item("打开今日确认页", #selector(openReview)))
        menu.addItem(item("生成今日日报", #selector(generateDaily)))
        menu.addItem(item("打开本地数据目录", #selector(openDataDir)))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(item("退出", #selector(quit)))
        statusItem.menu = menu
    }

    private func item(_ title: String, _ selector: Selector) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: "")
        item.target = self
        return item
    }

    @objc private func syncNow() {
        runShell("cd '\(projectRoot)' && if [ -f ~/.config/work-journal-agent/secrets.env ]; then source ~/.config/work-journal-agent/secrets.env; fi; PYTHONPATH=src python3 -m work_journal_agent sync")
    }

    @objc private func openReview() {
        runShell("if curl -fsS http://127.0.0.1:8765/api/status >/dev/null 2>&1; then open http://127.0.0.1:8765/review/today; else cd '\(projectRoot)' && if [ -f ~/.config/work-journal-agent/secrets.env ]; then source ~/.config/work-journal-agent/secrets.env; fi; PYTHONPATH=src python3 -m work_journal_agent requirements review; fi")
    }

    @objc private func generateDaily() {
        runShell("cd '\(projectRoot)' && if [ -f ~/.config/work-journal-agent/secrets.env ]; then source ~/.config/work-journal-agent/secrets.env; fi; PYTHONPATH=src python3 -m work_journal_agent generate-daily")
    }

    @objc private func openDataDir() {
        runShell("open ~/.local/share/work-journal-agent")
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func runShell(_ command: String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = ["-lc", command + " >/tmp/work-journal-agent-menubar.log 2>&1 &"]
        try? process.run()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
'''
print(template.replace("__PROJECT_ROOT__", json.dumps(root)))
PY

swiftc "${SWIFT_PATH}" -o "${BIN_PATH}" -framework AppKit
chmod +x "${BIN_PATH}"
open "${APP_DIR}"
echo "Installed and opened ${APP_DIR}"
