import AppKit
import Combine
import Darwin
import Foundation
import SwiftUI

private enum AppPaths {
    static let home = FileManager.default.homeDirectoryForCurrentUser
    static let configPath = home.appendingPathComponent(".config/work-journal-agent/config.toml")
    static let secretsPath = home.appendingPathComponent(".config/work-journal-agent/secrets.env")
    static let dataDir = home.appendingPathComponent(".local/share/work-journal-agent")
    static let launchAgentPath = home.appendingPathComponent("Library/LaunchAgents/com.shaoyang01.work-journal-agent.daily.plist")

    static var projectRoot: String {
        if let url = Bundle.main.url(forResource: "project-root", withExtension: "txt"),
           let text = try? String(contentsOf: url, encoding: .utf8) {
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                if trimmed.hasPrefix("@BUNDLE_RESOURCES@/"),
                   let resourceURL = Bundle.main.resourceURL {
                    let relative = String(trimmed.dropFirst("@BUNDLE_RESOURCES@/".count))
                    return resourceURL.appendingPathComponent(relative).path
                }
                return trimmed
            }
        }
        return FileManager.default.currentDirectoryPath
    }
}

private struct WorkJournalConfig {
    var databasePath = "~/.local/share/work-journal-agent/work-journal.db"
    var inboxPath = "~/.local/share/work-journal-agent/inbox/events.jsonl"
    var outputDir = "~/.local/share/work-journal-agent/out"

    var vaultPath = ""
    var dailyDir = "Daily"
    var taskDir = "Tasks"
    var writeTaskNotes = true
    var knowledgeDir = "Knowledge"
    var writeKnowledgeNotes = false

    var aiEnabled = false
    var provider = "deepseek"
    var baseUrl = "https://api.deepseek.com"
    var model = "deepseek-v4-pro"
    var apiKeyEnv = "DEEPSEEK_API_KEY"
    var timeoutSeconds = "180"
    var cacheEnabled = true
    var cacheRetentionDays = "7"
    var clusterReviewEnabled = true
    var clusterReviewTimeoutSeconds = "240"
    var clusterReviewMinConfidence = "0.75"
    var knowledgeEnabled = false

    var codexEnabled = true
    var codexSessionsRoot = "~/.codex/sessions"
    var claudeEnabled = false
    var claudeSettingsPath = "~/.claude/settings.json"
    var opencodeEnabled = defaultOpenCodeEnabled()
    var opencodeStorageRoot = "~/.local/share/opencode/storage"
    var opencodePluginPath = "~/.config/opencode/plugins/work-journal-agent.js"
    var kunEnabled = defaultKunEnabled()
    var kunStorageRoot = "~/.kun/data"
    var kunProjectRoot = AppPaths.projectRoot
    var zcodeEnabled = defaultZCodeEnabled()
    var zcodeStorageRoot = "~/.zcode/cli"

    static func load() -> WorkJournalConfig {
        var config = WorkJournalConfig()
        guard let text = try? String(contentsOf: AppPaths.configPath, encoding: .utf8) else {
            return config
        }
        let sections = parseTomlSections(text)
        config.databasePath = sections.value("storage", "database_path", config.databasePath)
        config.inboxPath = sections.value("storage", "inbox_path", config.inboxPath)
        config.outputDir = sections.value("storage", "output_dir", config.outputDir)

        config.vaultPath = sections.value("obsidian", "vault_path", config.vaultPath)
        config.dailyDir = sections.value("obsidian", "daily_dir", config.dailyDir)
        config.taskDir = sections.value("obsidian", "task_dir", config.taskDir)
        config.writeTaskNotes = true
        config.knowledgeDir = sections.value("obsidian", "knowledge_dir", config.knowledgeDir)
        config.writeKnowledgeNotes = sections.bool("obsidian", "write_knowledge_notes", config.writeKnowledgeNotes)

        config.aiEnabled = sections.bool("ai", "enabled", config.aiEnabled)
        config.provider = sections.value("ai", "provider", config.provider)
        config.baseUrl = sections.value("ai", "base_url", config.baseUrl)
        config.model = sections.value("ai", "model", config.model)
        config.apiKeyEnv = sections.value("ai", "api_key_env", config.apiKeyEnv)
        config.timeoutSeconds = sections.value("ai", "timeout_seconds", config.timeoutSeconds)
        config.cacheEnabled = sections.bool("ai", "cache_enabled", config.cacheEnabled)
        config.cacheRetentionDays = sections.value("ai", "cache_retention_days", config.cacheRetentionDays)
        config.clusterReviewEnabled = sections.bool("ai", "cluster_review_enabled", config.clusterReviewEnabled)
        config.clusterReviewTimeoutSeconds = sections.value("ai", "cluster_review_timeout_seconds", config.clusterReviewTimeoutSeconds)
        config.clusterReviewMinConfidence = sections.value("ai", "cluster_review_min_confidence", config.clusterReviewMinConfidence)
        config.knowledgeEnabled = sections.bool("ai", "knowledge_enabled", config.knowledgeEnabled)

        config.codexEnabled = sections.bool("sources.codex", "enabled", config.codexEnabled)
        config.codexSessionsRoot = sections.value("sources.codex", "sessions_root", config.codexSessionsRoot)
        config.claudeEnabled = sections.bool("sources.claude", "enabled", config.claudeEnabled)
        config.claudeSettingsPath = sections.value("sources.claude", "settings_path", config.claudeSettingsPath)
        config.opencodeEnabled = sections.bool("sources.opencode", "enabled", config.opencodeEnabled)
        config.opencodeStorageRoot = sections.value("sources.opencode", "storage_root", config.opencodeStorageRoot)
        config.opencodePluginPath = sections.value("sources.opencode", "plugin_path", config.opencodePluginPath)
        config.kunEnabled = sections.bool("sources.kun", "enabled", config.kunEnabled)
        config.kunStorageRoot = sections.value("sources.kun", "storage_root", config.kunStorageRoot)
        config.kunProjectRoot = sections.value("sources.kun", "project_root", config.kunProjectRoot)
        config.zcodeEnabled = sections.bool("sources.zcode", "enabled", config.zcodeEnabled)
        config.zcodeStorageRoot = sections.value("sources.zcode", "storage_root", config.zcodeStorageRoot)
        return config
    }

    func toml() -> String {
        return """
        [storage]
        database_path = "\(tomlString(databasePath))"
        output_dir = "\(tomlString(outputDir))"

        [obsidian]
        vault_path = "\(tomlString(vaultPath))"
        daily_dir = "\(tomlString(dailyDir))"
        task_dir = "\(tomlString(taskDir))"
        write_task_notes = true
        knowledge_dir = "\(tomlString(knowledgeDir))"
        write_knowledge_notes = \(writeKnowledgeNotes.toml)

        [privacy]
        max_raw_request_chars = 500
        store_transcript_paths = true

        [merge]
        min_keyword_overlap = 1

        [ai]
        enabled = \(aiEnabled.toml)
        provider = "\(tomlString(provider))"
        base_url = "\(tomlString(baseUrl))"
        model = "\(tomlString(model))"
        api_key_env = "\(tomlString(apiKeyEnv))"
        timeout_seconds = \(intText(timeoutSeconds, fallback: "180"))
        cache_enabled = \(cacheEnabled.toml)
        cache_retention_days = \(intText(cacheRetentionDays, fallback: "7"))
        cluster_review_enabled = \(clusterReviewEnabled.toml)
        cluster_review_timeout_seconds = \(intText(clusterReviewTimeoutSeconds, fallback: "240"))
        cluster_review_min_confidence = \(doubleText(clusterReviewMinConfidence, fallback: "0.75"))
        knowledge_enabled = \(knowledgeEnabled.toml)

        [sources.codex]
        enabled = \(codexEnabled.toml)
        sessions_root = "\(tomlString(codexSessionsRoot))"

        [sources.claude]
        enabled = \(claudeEnabled.toml)
        settings_path = "\(tomlString(claudeSettingsPath))"

        [sources.opencode]
        enabled = \(opencodeEnabled.toml)
        storage_root = "\(tomlString(opencodeStorageRoot))"
        plugin_path = "\(tomlString(opencodePluginPath))"

        [sources.kun]
        enabled = \(kunEnabled.toml)
        storage_root = "\(tomlString(kunStorageRoot))"
        project_root = "\(tomlString(kunProjectRoot))"

        [sources.zcode]
        enabled = \(zcodeEnabled.toml)
        storage_root = "\(tomlString(zcodeStorageRoot))"
        """
    }
}

private final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var preferencesController: PreferencesWindowController?
    private var reviewController: ReviewWindowController?
    private var requirementManagerController: RequirementManagerWindowController?
    private let projectRoot = AppPaths.projectRoot

    func applicationDidFinishLaunching(_ notification: Notification) {
        installMainMenu()
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.title = "WJ"
            button.image = NSImage(systemSymbolName: "book.closed.fill", accessibilityDescription: "Work Journal Agent")
            button.imagePosition = .imageLeading
            button.toolTip = "Work Journal Agent"
        }
        rebuildMenu()
        if ProcessInfo.processInfo.arguments.contains("--open-review") {
            DispatchQueue.main.async { [weak self] in
                self?.openReview()
            }
        }
    }

    private func installMainMenu() {
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(NSMenuItem(title: "退出 Work Journal Agent", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)

        let editMenuItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(NSMenuItem(title: "撤销", action: Selector(("undo:")), keyEquivalent: "z"))
        let redo = NSMenuItem(title: "重做", action: Selector(("redo:")), keyEquivalent: "Z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(redo)
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(NSMenuItem(title: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        editMenu.addItem(NSMenuItem(title: "复制", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        editMenu.addItem(NSMenuItem(title: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        editMenu.addItem(NSMenuItem(title: "删除", action: #selector(NSText.delete(_:)), keyEquivalent: ""))
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(NSMenuItem(title: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
        editMenuItem.submenu = editMenu
        mainMenu.addItem(editMenuItem)

        NSApp.mainMenu = mainMenu
    }

    private func rebuildMenu() {
        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "Work Journal", action: nil, keyEquivalent: ""))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(item("最近 7 天日报/确认...", #selector(openReview)))
        menu.addItem(item("需求管理...", #selector(openRequirementManager)))
        menu.addItem(item("同步最新事件", #selector(syncNow)))
        menu.addItem(item("生成今日日报", #selector(generateDaily)))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(item("设置...", #selector(openSettings)))
        menu.addItem(item("打开本地数据目录", #selector(openDataDir)))
        menu.addItem(item("打开日志", #selector(openLog)))
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
        runShell("cd \(shellQuote(projectRoot)) && if [ -f ~/.config/work-journal-agent/secrets.env ]; then source ~/.config/work-journal-agent/secrets.env; fi; PYTHONPATH=src python3 -m work_journal_agent sync")
    }

    @objc private func openReview() {
        if reviewController == nil {
            reviewController = ReviewWindowController(projectRoot: projectRoot)
        }
        reviewController?.showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func openRequirementManager() {
        if requirementManagerController == nil {
            requirementManagerController = RequirementManagerWindowController(projectRoot: projectRoot)
        }
        requirementManagerController?.showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func generateDaily() {
        runShell("cd \(shellQuote(projectRoot)) && if [ -f ~/.config/work-journal-agent/secrets.env ]; then source ~/.config/work-journal-agent/secrets.env; fi; PYTHONPATH=src python3 -m work_journal_agent generate-daily")
    }

    @objc private func openSettings() {
        if preferencesController == nil {
            preferencesController = PreferencesWindowController(projectRoot: projectRoot)
        }
        preferencesController?.showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func openDataDir() {
        runShell("mkdir -p ~/.local/share/work-journal-agent && open ~/.local/share/work-journal-agent")
    }

    @objc private func openLog() {
        runShell("mkdir -p ~/.local/share/work-journal-agent/logs; touch /tmp/work-journal-agent-menubar.log; open /tmp/work-journal-agent-menubar.log")
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

private final class PreferencesWindowController: NSWindowController, NSTextFieldDelegate {
    private let projectRoot: String
    private let documentHeight: CGFloat = 1680
    private var dirty = false
    private var config = WorkJournalConfig.load()
    private var sectionOrigins: [String: CGFloat] = [:]

    private let rootView = NSView()
    private let sidebarView = NSView()
    private let scrollView = NSScrollView()
    private let documentView = NSView()
    private let footerView = NSView()
    private let saveButton = NSButton()
    private let revertButton = NSButton()
    private let statusLabel = NSTextField(labelWithString: "")

    private var databaseField = NSTextField()
    private var outputField = NSTextField()
    private var vaultField = NSTextField()
    private var dailyField = NSTextField()
    private var taskField = NSTextField()
    private var knowledgeField = NSTextField()
    private var writeTaskSwitch = NSSwitch()
    private var writeKnowledgeSwitch = NSSwitch()
    private var codexSwitch = NSSwitch()
    private var codexSessionsField = NSTextField()
    private var claudeSwitch = NSSwitch()
    private var claudeSettingsField = NSTextField()
    private var opencodeSwitch = NSSwitch()
    private var opencodeStorageField = NSTextField()
    private var opencodePluginField = NSTextField()
    private var aiSwitch = NSSwitch()
    private var providerField = NSTextField()
    private var baseUrlField = NSTextField()
    private var modelField = NSTextField()
    private var apiKeyField = NSSecureTextField()
    private var timeoutField = NSTextField()
    private var clusterTimeoutField = NSTextField()
    private var cacheSwitch = NSSwitch()
    private var cacheDaysField = NSTextField()
    private var clusterSwitch = NSSwitch()
    private var clusterConfidenceField = NSTextField()
    private var aiKnowledgeSwitch = NSSwitch()

    init(projectRoot: String) {
        self.projectRoot = projectRoot
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1120, height: 820),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Work Journal Agent 设置"
        window.minSize = NSSize(width: 1060, height: 760)
        super.init(window: window)
        window.center()
        buildWindow()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func showWindow(_ sender: Any?) {
        super.showWindow(sender)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func buildWindow() {
        guard let window else { return }
        let view = WorkJournalSettingsView(projectRoot: projectRoot)
            .frame(minWidth: 1060, minHeight: 760)
        window.contentView = NSHostingView(rootView: view)
    }

    private func buildSidebar() {
        let title = NSTextField(labelWithString: "Work Journal")
        title.frame = NSRect(x: 22, y: 606, width: 140, height: 22)
        title.font = NSFont.boldSystemFont(ofSize: 15)
        title.textColor = Color.primaryText
        sidebarView.addSubview(title)

        let subtitle = NSTextField(labelWithString: "本机设置")
        subtitle.frame = NSRect(x: 22, y: 584, width: 140, height: 18)
        subtitle.font = NSFont.systemFont(ofSize: 12)
        subtitle.textColor = Color.secondaryText
        sidebarView.addSubview(subtitle)

        let items = [
            ("总览", "gauge.medium", "overview"),
            ("Obsidian", "book.pages", "obsidian"),
            ("数据源", "point.3.connected.trianglepath.dotted", "sources"),
            ("AI", "sparkles", "ai"),
            ("路径", "folder", "paths"),
            ("日志", "doc.text.magnifyingglass", "logs"),
        ]
        var y: CGFloat = 532
        for (label, icon, identifier) in items {
            let button = sidebarButton(title: label, symbolName: icon, identifier: identifier)
            button.frame = NSRect(x: 14, y: y, width: 160, height: 34)
            sidebarView.addSubview(button)
            y -= 42
        }
    }

    private func buildDocument() {
        documentView.subviews.forEach { $0.removeFromSuperview() }
        var y: CGFloat = 1624
        addHeader(y: y)
        y -= 118
        addOverviewSection(y: y)
        sectionOrigins["overview"] = scrollOrigin(sectionTopY: y)
        y -= 198
        addObsidianSection(y: y)
        sectionOrigins["obsidian"] = scrollOrigin(sectionTopY: y)
        y -= 286
        addSourcesSection(y: y)
        sectionOrigins["sources"] = scrollOrigin(sectionTopY: y)
        y -= 286
        addAISection(y: y)
        sectionOrigins["ai"] = scrollOrigin(sectionTopY: y)
        y -= 304
        addPathsSection(y: y)
        sectionOrigins["paths"] = scrollOrigin(sectionTopY: y)
        y -= 204
        addLogsSection(y: y)
        sectionOrigins["logs"] = scrollOrigin(sectionTopY: y)
    }

    private func addHeader(y: CGFloat) {
        let title = NSTextField(labelWithString: "本机配置")
        title.frame = NSRect(x: 34, y: y, width: 240, height: 32)
        title.font = NSFont.boldSystemFont(ofSize: 24)
        title.textColor = Color.primaryText
        documentView.addSubview(title)

        let subtitle = NSTextField(labelWithString: "配置 Work Journal Agent 如何读取本机事件、写入 Obsidian，并可选启用 DeepSeek 分析。")
        subtitle.frame = NSRect(x: 34, y: y - 30, width: 650, height: 20)
        subtitle.font = NSFont.systemFont(ofSize: 13)
        subtitle.textColor = Color.secondaryText
        documentView.addSubview(subtitle)

        let path = NSTextField(labelWithString: AppPaths.configPath.path)
        path.frame = NSRect(x: 34, y: y - 58, width: 650, height: 18)
        path.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        path.textColor = Color.mutedText
        documentView.addSubview(path)
    }

    private func addOverviewSection(y: CGFloat) {
        let section = sectionView(title: "总览", subtitle: "管理 Work Journal Agent 的本机运行配置，安装到新机器后界面保持一致。", y: y, height: 174)
        let chips = [
            ("配置文件", FileManager.default.fileExists(atPath: AppPaths.configPath.path) ? "已创建" : "未创建", Color.blue),
            ("Obsidian", config.vaultPath.isEmpty ? "未配置" : "已配置", config.vaultPath.isEmpty ? Color.orange : Color.green),
            ("AI", config.aiEnabled ? "已启用" : "关闭", config.aiEnabled ? Color.green : Color.mutedChip),
            ("自动同步", FileManager.default.fileExists(atPath: AppPaths.launchAgentPath.path) ? "已安装" : "未安装", Color.mutedChip),
        ]
        var x: CGFloat = 22
        for chip in chips {
            let view = statusChip(label: chip.0, value: chip.1, color: chip.2)
            view.frame = NSRect(x: x, y: 82, width: 150, height: 38)
            section.addSubview(view)
            x += 162
        }
        documentView.addSubview(section)
    }

    private func addObsidianSection(y: CGFloat) {
        let section = sectionView(title: "Obsidian", subtitle: "设置日报写入位置和笔记目录。", y: y, height: 262)
        vaultField = textField(width: 478)
        addRow(to: section, y: 178, label: "Vault 路径", control: vaultField, button: chooseButton("选择...", "vault"))
        dailyField = textField(width: 170)
        taskField = textField(width: 170)
        knowledgeField = textField(width: 170)
        addInlineFields(to: section, y: 126, fields: [("Daily 目录", dailyField), ("Tasks 目录", taskField), ("Knowledge 目录", knowledgeField)])
        writeKnowledgeSwitch = switchControl()
        addSwitchRow(to: section, y: 52, label: "写入 Knowledge 笔记", detail: "实验功能，生成知识专题笔记。", control: writeKnowledgeSwitch)
        documentView.addSubview(section)
    }

    private func addSourcesSection(y: CGFloat) {
        let section = sectionView(title: "数据源", subtitle: "选择要采集的本机 Agent 事件源。", y: y, height: 262)
        codexSwitch = switchControl()
        codexSessionsField = textField(width: 440)
        addSourceRow(to: section, y: 174, title: "Codex", detail: "读取 ~/.codex/sessions 中的会话事件。", toggle: codexSwitch, field: codexSessionsField, button: chooseButton("选择...", "codexSessions"), status: pathStatus(config.codexSessionsRoot, directory: true))
        claudeSwitch = switchControl()
        claudeSettingsField = textField(width: 440)
        addSourceRow(to: section, y: 98, title: "Claude Code", detail: "配置 hooks 写入 Work Journal 数据库。", toggle: claudeSwitch, field: claudeSettingsField, button: chooseButton("选择...", "claudeSettings"), status: pathStatus(config.claudeSettingsPath, directory: false))
        opencodeSwitch = switchControl()
        opencodeStorageField = textField(width: 440)
        addSourceRow(to: section, y: 22, title: "OpenCode", detail: "导入 OpenCode 本地 storage 事件。", toggle: opencodeSwitch, field: opencodeStorageField, button: chooseButton("选择...", "opencodeStorage"), status: pathStatus(config.opencodeStorageRoot, directory: true))
        documentView.addSubview(section)
    }

    private func addAISection(y: CGFloat) {
        let section = sectionView(title: "AI", subtitle: "可选启用 DeepSeek，复核需求聚类并整理每日摘要。", y: y, height: 282)
        aiSwitch = switchControl()
        addSwitchRow(to: section, y: 204, label: "启用 DeepSeek", detail: "只发送压缩后的任务事件，不上传完整聊天全文。", control: aiSwitch)

        providerField = textField(width: 150)
        modelField = textField(width: 200)
        baseUrlField = textField(width: 250)
        addInlineFields(to: section, y: 150, fields: [("Provider", providerField), ("Model", modelField), ("Base URL", baseUrlField)])

        apiKeyField = secureField(width: 352)
        timeoutField = textField(width: 90)
        clusterTimeoutField = textField(width: 110)
        cacheDaysField = textField(width: 90)
        addInlineFields(to: section, y: 96, fields: [("API Key", apiKeyField), ("通用超时", timeoutField), ("聚类超时", clusterTimeoutField), ("缓存天数", cacheDaysField)])

        cacheSwitch = switchControl()
        clusterSwitch = switchControl()
        aiKnowledgeSwitch = switchControl()
        addMiniSwitch(to: section, x: 22, y: 36, label: "启用缓存", control: cacheSwitch)
        addMiniSwitch(to: section, x: 190, y: 36, label: "需求聚类复核", control: clusterSwitch)
        addMiniSwitch(to: section, x: 386, y: 36, label: "Knowledge AI", control: aiKnowledgeSwitch)
        documentView.addSubview(section)
    }

    private func addPathsSection(y: CGFloat) {
        let section = sectionView(title: "路径", subtitle: "配置 SQLite 数据库、备用输出目录和 OpenCode 插件文件。", y: y, height: 232)
        databaseField = textField(width: 478)
        outputField = textField(width: 478)
        opencodePluginField = textField(width: 478)
        addRow(to: section, y: 146, label: "SQLite 数据库", control: databaseField, button: chooseButton("选择...", "database"))
        addRow(to: section, y: 94, label: "备用输出目录", control: outputField, button: chooseButton("选择...", "output"))
        addRow(to: section, y: 42, label: "OpenCode 插件", control: opencodePluginField, button: chooseButton("选择...", "opencodePlugin"))
        documentView.addSubview(section)
    }

    private func addLogsSection(y: CGFloat) {
        let section = sectionView(title: "日志", subtitle: "打开本机日志和运行数据，便于排查同步问题。", y: y, height: 146)
        addInfoLine(to: section, y: 72, label: "菜单日志", value: "/tmp/work-journal-agent-menubar.log")
        addInfoLine(to: section, y: 36, label: "LaunchAgent", value: AppPaths.launchAgentPath.path)
        documentView.addSubview(section)
    }

    private func buildFooter() {
        statusLabel.frame = NSRect(x: 24, y: 26, width: 278, height: 20)
        statusLabel.font = NSFont.systemFont(ofSize: 12)
        statusLabel.textColor = Color.secondaryText
        footerView.addSubview(statusLabel)

        saveButton.title = "保存配置"
        saveButton.bezelStyle = .rounded
        saveButton.frame = NSRect(x: 312, y: 20, width: 104, height: 32)
        saveButton.target = self
        saveButton.action = #selector(saveConfig)
        footerView.addSubview(saveButton)

        revertButton.title = "还原"
        revertButton.bezelStyle = .rounded
        revertButton.frame = NSRect(x: 424, y: 20, width: 74, height: 32)
        revertButton.target = self
        revertButton.action = #selector(revertConfig)
        footerView.addSubview(revertButton)

        let setupButton = footerButton("重新配置本机...", #selector(runSetupWizard), x: 504, width: 124)
        footerView.addSubview(setupButton)
        let dirButton = footerButton("打开配置目录", #selector(openConfigDirectory), x: 636, width: 104)
        footerView.addSubview(dirButton)
    }

    private func loadFields() {
        databaseField.stringValue = config.databasePath
        outputField.stringValue = config.outputDir
        vaultField.stringValue = config.vaultPath
        dailyField.stringValue = config.dailyDir
        taskField.stringValue = config.taskDir
        knowledgeField.stringValue = config.knowledgeDir
        writeKnowledgeSwitch.state = config.writeKnowledgeNotes ? .on : .off
        codexSwitch.state = config.codexEnabled ? .on : .off
        codexSessionsField.stringValue = config.codexSessionsRoot
        claudeSwitch.state = config.claudeEnabled ? .on : .off
        claudeSettingsField.stringValue = config.claudeSettingsPath
        opencodeSwitch.state = config.opencodeEnabled ? .on : .off
        opencodeStorageField.stringValue = config.opencodeStorageRoot
        opencodePluginField.stringValue = config.opencodePluginPath
        aiSwitch.state = config.aiEnabled ? .on : .off
        providerField.stringValue = config.provider
        baseUrlField.stringValue = config.baseUrl
        modelField.stringValue = config.model
        apiKeyField.stringValue = ""
        apiKeyField.placeholderString = FileManager.default.fileExists(atPath: AppPaths.secretsPath.path) ? "已保存，留空保持不变" : "未保存"
        timeoutField.stringValue = config.timeoutSeconds
        clusterTimeoutField.stringValue = config.clusterReviewTimeoutSeconds
        cacheSwitch.state = config.cacheEnabled ? .on : .off
        cacheDaysField.stringValue = config.cacheRetentionDays
        clusterSwitch.state = config.clusterReviewEnabled ? .on : .off
        clusterConfidenceField.stringValue = config.clusterReviewMinConfidence
        aiKnowledgeSwitch.state = config.knowledgeEnabled ? .on : .off
        dirty = false
    }

    @objc private func saveConfig() {
        var next = WorkJournalConfig()
        next.databasePath = databaseField.stringValue
        next.inboxPath = config.inboxPath
        next.outputDir = outputField.stringValue
        next.vaultPath = vaultField.stringValue
        next.dailyDir = dailyField.stringValue
        next.taskDir = taskField.stringValue
        next.knowledgeDir = knowledgeField.stringValue
        next.writeTaskNotes = true
        next.writeKnowledgeNotes = writeKnowledgeSwitch.state == .on
        next.codexEnabled = codexSwitch.state == .on
        next.codexSessionsRoot = codexSessionsField.stringValue
        next.claudeEnabled = claudeSwitch.state == .on
        next.claudeSettingsPath = claudeSettingsField.stringValue
        next.opencodeEnabled = opencodeSwitch.state == .on
        next.opencodeStorageRoot = opencodeStorageField.stringValue
        next.opencodePluginPath = opencodePluginField.stringValue
        next.aiEnabled = aiSwitch.state == .on
        next.provider = providerField.stringValue
        next.baseUrl = baseUrlField.stringValue
        next.model = modelField.stringValue
        next.timeoutSeconds = timeoutField.stringValue
        next.clusterReviewTimeoutSeconds = clusterTimeoutField.stringValue
        next.cacheEnabled = cacheSwitch.state == .on
        next.cacheRetentionDays = cacheDaysField.stringValue
        next.clusterReviewEnabled = clusterSwitch.state == .on
        next.clusterReviewMinConfidence = config.clusterReviewMinConfidence
        next.knowledgeEnabled = aiKnowledgeSwitch.state == .on

        do {
            try FileManager.default.createDirectory(at: AppPaths.configPath.deletingLastPathComponent(), withIntermediateDirectories: true)
            try next.toml().write(to: AppPaths.configPath, atomically: true, encoding: .utf8)
            createRuntimeDirectories(for: next)
            if !apiKeyField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                try saveSecret(apiKeyField.stringValue)
                apiKeyField.stringValue = ""
                apiKeyField.placeholderString = "已保存，留空保持不变"
            }
            config = next
            dirty = false
            updateStatus(message: "已保存配置")
        } catch {
            updateStatus(message: "保存失败：\(error.localizedDescription)", error: true)
        }
    }

    @objc private func revertConfig() {
        config = WorkJournalConfig.load()
        loadFields()
        updateStatus(message: "已还原为磁盘配置")
    }

    @objc private func runSetupWizard() {
        runTerminal("cd \(shellQuote(projectRoot)) && PYTHONPATH=src python3 -m work_journal_agent setup")
    }

    @objc private func openConfigDirectory() {
        try? FileManager.default.createDirectory(at: AppPaths.configPath.deletingLastPathComponent(), withIntermediateDirectories: true)
        NSWorkspace.shared.open(AppPaths.configPath.deletingLastPathComponent())
    }

    private func updateStatus(message: String? = nil, error: Bool = false) {
        let configStatus = FileManager.default.fileExists(atPath: AppPaths.configPath.path) ? "配置文件已创建" : "配置文件未创建"
        let dirtyStatus = dirty ? " · 有未保存修改" : ""
        statusLabel.stringValue = message ?? "\(configStatus)\(dirtyStatus)"
        statusLabel.textColor = error ? Color.red : (dirty ? Color.orange : Color.secondaryText)
    }

    @objc private func markDirty(_ sender: Any?) {
        dirty = true
        updateStatus()
    }

    func controlTextDidChange(_ obj: Notification) {
        markDirty(nil)
    }

    @objc private func choosePath(_ sender: NSButton) {
        guard let id = sender.identifier?.rawValue else { return }
        let panel = NSOpenPanel()
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.canChooseFiles = id == "claudeSettings" || id == "opencodePlugin" || id == "database"
        panel.canChooseDirectories = !panel.canChooseFiles
        if panel.runModal() == .OK, let path = panel.url?.path {
            field(for: id)?.stringValue = path
            markDirty(nil)
        }
    }

    @objc private func scrollToSection(_ sender: NSButton) {
        guard let id = sender.identifier?.rawValue, let y = sectionOrigins[id] else { return }
        scrollView.contentView.scroll(to: NSPoint(x: 0, y: max(0, y)))
        scrollView.reflectScrolledClipView(scrollView.contentView)
    }

    private func scrollToTop() {
        let visibleHeight = scrollView.contentView.bounds.height
        let y = max(0, documentHeight - visibleHeight)
        scrollView.contentView.scroll(to: NSPoint(x: 0, y: y))
        scrollView.reflectScrolledClipView(scrollView.contentView)
    }

    private func scrollOrigin(sectionTopY: CGFloat) -> CGFloat {
        let visibleHeight = max(scrollView.contentView.bounds.height, 588)
        let maxY = max(0, documentHeight - visibleHeight)
        return min(max(0, sectionTopY - visibleHeight + 56), maxY)
    }

    private func field(for identifier: String) -> NSTextField? {
        switch identifier {
        case "vault": return vaultField
        case "codexSessions": return codexSessionsField
        case "claudeSettings": return claudeSettingsField
        case "opencodeStorage": return opencodeStorageField
        case "opencodePlugin": return opencodePluginField
        case "database": return databaseField
        case "output": return outputField
        default: return nil
        }
    }

    private func sectionView(title: String, subtitle: String, y: CGFloat, height: CGFloat) -> NSView {
        let view = NSView(frame: NSRect(x: 34, y: y - height, width: 674, height: height))
        view.wantsLayer = true
        view.layer?.backgroundColor = Color.group.cgColor
        view.layer?.cornerRadius = 12
        view.layer?.borderColor = Color.border.cgColor
        view.layer?.borderWidth = 1

        let titleLabel = NSTextField(labelWithString: title)
        titleLabel.frame = NSRect(x: 22, y: height - 36, width: 220, height: 22)
        titleLabel.font = NSFont.boldSystemFont(ofSize: 15)
        titleLabel.textColor = Color.primaryText
        view.addSubview(titleLabel)

        let subtitleLabel = NSTextField(labelWithString: subtitle)
        subtitleLabel.frame = NSRect(x: 22, y: height - 58, width: 610, height: 18)
        subtitleLabel.font = NSFont.systemFont(ofSize: 12)
        subtitleLabel.textColor = Color.secondaryText
        view.addSubview(subtitleLabel)
        return view
    }

    private func addRow(to section: NSView, y: CGFloat, label: String, control: NSTextField, button: NSButton? = nil) {
        let labelView = fieldLabel(label)
        labelView.frame = NSRect(x: 22, y: y + 8, width: 112, height: 18)
        section.addSubview(labelView)
        control.frame = NSRect(x: 142, y: y, width: control.frame.width, height: 32)
        section.addSubview(control)
        if let button {
            button.frame = NSRect(x: 142 + control.frame.width + 10, y: y, width: 76, height: 32)
            section.addSubview(button)
        }
    }

    private func addInlineFields(to section: NSView, y: CGFloat, fields: [(String, NSTextField)]) {
        var x: CGFloat = 22
        for (label, field) in fields {
            let labelView = fieldLabel(label)
            labelView.frame = NSRect(x: x, y: y + 38, width: field.frame.width, height: 16)
            section.addSubview(labelView)
            field.frame = NSRect(x: x, y: y, width: field.frame.width, height: 32)
            section.addSubview(field)
            x += field.frame.width + 18
        }
    }

    private func addSwitchRow(to section: NSView, y: CGFloat, label: String, detail: String, control: NSSwitch) {
        control.frame = NSRect(x: 22, y: y + 4, width: 52, height: 32)
        section.addSubview(control)
        let title = NSTextField(labelWithString: label)
        title.frame = NSRect(x: 86, y: y + 19, width: 250, height: 18)
        title.font = NSFont.systemFont(ofSize: 13, weight: .medium)
        title.textColor = Color.primaryText
        section.addSubview(title)
        let detailLabel = NSTextField(labelWithString: detail)
        detailLabel.frame = NSRect(x: 86, y: y, width: 520, height: 18)
        detailLabel.font = NSFont.systemFont(ofSize: 12)
        detailLabel.textColor = Color.secondaryText
        section.addSubview(detailLabel)
    }

    private func addSourceRow(to section: NSView, y: CGFloat, title: String, detail: String, toggle: NSSwitch, field: NSTextField, button: NSButton, status: String) {
        toggle.frame = NSRect(x: 22, y: y + 22, width: 52, height: 32)
        section.addSubview(toggle)
        let titleLabel = NSTextField(labelWithString: title)
        titleLabel.frame = NSRect(x: 86, y: y + 44, width: 150, height: 18)
        titleLabel.font = NSFont.systemFont(ofSize: 13, weight: .medium)
        titleLabel.textColor = Color.primaryText
        section.addSubview(titleLabel)
        let statusLabel = badge(status)
        statusLabel.frame = NSRect(x: 238, y: y + 41, width: 72, height: 22)
        section.addSubview(statusLabel)
        let detailLabel = NSTextField(labelWithString: detail)
        detailLabel.frame = NSRect(x: 86, y: y + 20, width: 520, height: 18)
        detailLabel.font = NSFont.systemFont(ofSize: 12)
        detailLabel.textColor = Color.secondaryText
        section.addSubview(detailLabel)
        field.frame = NSRect(x: 86, y: y - 18, width: 440, height: 30)
        section.addSubview(field)
        button.frame = NSRect(x: 536, y: y - 18, width: 76, height: 30)
        section.addSubview(button)
    }

    private func addMiniSwitch(to section: NSView, x: CGFloat, y: CGFloat, label: String, control: NSSwitch) {
        control.frame = NSRect(x: x, y: y - 4, width: 52, height: 32)
        section.addSubview(control)
        let title = NSTextField(labelWithString: label)
        title.frame = NSRect(x: x + 58, y: y + 3, width: 130, height: 18)
        title.font = NSFont.systemFont(ofSize: 13)
        title.textColor = Color.primaryText
        section.addSubview(title)
    }

    private func addInfoLine(to section: NSView, y: CGFloat, label: String, value: String) {
        let labelView = fieldLabel(label)
        labelView.frame = NSRect(x: 22, y: y, width: 112, height: 18)
        section.addSubview(labelView)
        let valueView = NSTextField(labelWithString: value)
        valueView.frame = NSRect(x: 142, y: y, width: 500, height: 18)
        valueView.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        valueView.textColor = Color.secondaryText
        section.addSubview(valueView)
    }

    private func sidebarButton(title: String, symbolName: String, identifier: String) -> NSButton {
        let button = NSButton(title: title, target: self, action: #selector(scrollToSection))
        button.identifier = NSUserInterfaceItemIdentifier(identifier)
        button.bezelStyle = .inline
        button.isBordered = false
        button.alignment = .left
        button.font = NSFont.systemFont(ofSize: 13)
        button.contentTintColor = Color.primaryText
        button.image = NSImage(systemSymbolName: symbolName, accessibilityDescription: title)
        button.imagePosition = .imageLeading
        return button
    }

    private func textField(width: CGFloat) -> NSTextField {
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: width, height: 32))
        field.font = NSFont.systemFont(ofSize: 13)
        field.textColor = Color.primaryText
        field.backgroundColor = Color.input
        field.delegate = self
        return field
    }

    private func secureField(width: CGFloat) -> NSSecureTextField {
        let field = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: width, height: 32))
        field.font = NSFont.systemFont(ofSize: 13)
        field.textColor = Color.primaryText
        field.backgroundColor = Color.input
        field.delegate = self
        return field
    }

    private func switchControl() -> NSSwitch {
        let control = NSSwitch()
        control.target = self
        control.action = #selector(markDirty)
        return control
    }

    private func chooseButton(_ title: String, _ identifier: String) -> NSButton {
        let button = NSButton(title: title, target: self, action: #selector(choosePath))
        button.identifier = NSUserInterfaceItemIdentifier(identifier)
        button.bezelStyle = .rounded
        return button
    }

    private func footerButton(_ title: String, _ action: Selector, x: CGFloat, width: CGFloat) -> NSButton {
        let button = NSButton(title: title, target: self, action: action)
        button.bezelStyle = .rounded
        button.frame = NSRect(x: x, y: 20, width: width, height: 32)
        return button
    }

    private func fieldLabel(_ text: String) -> NSTextField {
        let label = NSTextField(labelWithString: text)
        label.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        label.textColor = Color.secondaryText
        return label
    }

    private func badge(_ text: String) -> NSTextField {
        let label = NSTextField(labelWithString: text)
        label.alignment = .center
        label.font = NSFont.systemFont(ofSize: 11, weight: .medium)
        label.textColor = Color.primaryText
        label.wantsLayer = true
        label.layer?.backgroundColor = Color.badge.cgColor
        label.layer?.cornerRadius = 10
        return label
    }

    private func statusChip(label: String, value: String, color: NSColor) -> NSView {
        let view = NSView()
        view.wantsLayer = true
        view.layer?.backgroundColor = Color.input.cgColor
        view.layer?.cornerRadius = 10
        let dot = NSView(frame: NSRect(x: 12, y: 14, width: 8, height: 8))
        dot.wantsLayer = true
        dot.layer?.backgroundColor = color.cgColor
        dot.layer?.cornerRadius = 4
        view.addSubview(dot)
        let title = NSTextField(labelWithString: label)
        title.frame = NSRect(x: 28, y: 18, width: 110, height: 14)
        title.font = NSFont.systemFont(ofSize: 11)
        title.textColor = Color.secondaryText
        view.addSubview(title)
        let detail = NSTextField(labelWithString: value)
        detail.frame = NSRect(x: 28, y: 5, width: 110, height: 16)
        detail.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        detail.textColor = Color.primaryText
        view.addSubview(detail)
        return view
    }

    private func pathStatus(_ text: String, directory: Bool) -> String {
        let expanded = expandTilde(text)
        let exists = FileManager.default.fileExists(atPath: expanded)
        return exists ? "已找到" : "未找到"
    }

    private func runTerminal(_ command: String) {
        let escaped = command.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
        let script = """
        tell application "Terminal"
          activate
          do script "\(escaped)"
        end tell
        """
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-e", script]
        try? process.run()
    }
}

private final class ReviewWindowController: NSWindowController {
    init(projectRoot: String) {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1180, height: 780),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Work Journal Agent 需求确认"
        window.minSize = NSSize(width: 1080, height: 720)
        super.init(window: window)
        window.center()
        window.contentView = NSHostingView(rootView: RequirementReviewView(projectRoot: projectRoot))
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func showWindow(_ sender: Any?) {
        super.showWindow(sender)
        NSApp.activate(ignoringOtherApps: true)
    }
}

private final class RequirementManagerWindowController: NSWindowController {
    init(projectRoot: String) {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1100, height: 740),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Work Journal Agent 需求管理"
        window.minSize = NSSize(width: 1040, height: 680)
        super.init(window: window)
        window.center()
        window.contentView = NSHostingView(rootView: RequirementManagerView(projectRoot: projectRoot))
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func showWindow(_ sender: Any?) {
        super.showWindow(sender)
        NSApp.activate(ignoringOtherApps: true)
    }
}

private struct NativeReviewSummary: Codable {
    var totalCandidates: Int
    var pendingCandidates: Int
    var eventCount: Int
}

private struct NativeReviewPayload: Codable {
    var date: String
    var generatedAt: String
    var candidates: [NativeReviewCandidate]
    var requirements: [NativeRequirementOption]
    var summary: NativeReviewSummary
}

private struct NativeRequirementOption: Codable, Identifiable {
    var id: String
    var title: String
    var project: String
    var requirementType: String
    var status: String
    var note: String?
    var createdAt: String
    var updatedAt: String
}

private struct NativeRequirementManagementSummary: Codable {
    var total: Int
    var active: Int
    var completed: Int
}

private struct NativeRequirementManagementPayload: Codable {
    var generatedAt: String
    var requirements: [NativeRequirementOption]
    var summary: NativeRequirementManagementSummary
}

private struct NativeReviewCandidate: Codable, Identifiable {
    var candidateId: String
    var requirementId: String
    var title: String
    var suggestedTitle: String
    var project: String
    var requirementType: String
    var status: String
    var confidence: Double
    var eventIds: [String]
    var eventCount: Int
    var sources: [String]
    var anchors: [String: [String]]
    var request: String
    var decision: String
    var files: [String]
    var reasons: [String]

    var id: String { candidateId }
}

private struct NativeReviewDecision: Codable {
    var candidateId: String
    var requirementId: String
    var title: String
    var project: String
    var requirementType: String
    var status: String
    var eventIds: [String]
    var anchors: [String: [String]]
}

private struct RequirementSearchComboBox: NSViewRepresentable {
    var requirements: [NativeRequirementOption]
    @Binding var selectedRequirementId: String
    var placeholder: String
    var onSelect: (String) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }

    func makeNSView(context: Context) -> NSComboBox {
        let comboBox = NSComboBox()
        comboBox.usesDataSource = true
        comboBox.completes = false
        comboBox.isEditable = true
        comboBox.numberOfVisibleItems = 10
        comboBox.dataSource = context.coordinator
        comboBox.delegate = context.coordinator
        comboBox.placeholderString = placeholder
        comboBox.font = NSFont.systemFont(ofSize: 14, weight: .semibold)
        comboBox.controlSize = .large
        comboBox.drawsBackground = true
        comboBox.backgroundColor = NSColor.hex(0x1A2026)
        comboBox.textColor = NSColor.hex(0xF4F6F8)
        return comboBox
    }

    func updateNSView(_ comboBox: NSComboBox, context: Context) {
        context.coordinator.parent = self
        context.coordinator.refreshFilter(query: comboBox.stringValue)
        comboBox.placeholderString = placeholder

        let isEditing = comboBox.window?.firstResponder === comboBox.currentEditor()
        if !isEditing {
            comboBox.stringValue = selectedDisplayText()
        }
        comboBox.reloadData()
    }

    private func selectedDisplayText() -> String {
        guard !selectedRequirementId.isEmpty,
              let requirement = requirements.first(where: { $0.id == selectedRequirementId }) else {
            return ""
        }
        return Self.displayText(for: requirement)
    }

    private static func displayText(for requirement: NativeRequirementOption) -> String {
        "\(requirement.title)  ·  \(requirement.project)"
    }

    final class Coordinator: NSObject, NSComboBoxDataSource, NSComboBoxDelegate, NSControlTextEditingDelegate {
        var parent: RequirementSearchComboBox
        private var filteredRequirements: [NativeRequirementOption]

        init(_ parent: RequirementSearchComboBox) {
            self.parent = parent
            self.filteredRequirements = parent.requirements
        }

        func numberOfItems(in comboBox: NSComboBox) -> Int {
            filteredRequirements.count
        }

        func comboBox(_ comboBox: NSComboBox, objectValueForItemAt index: Int) -> Any? {
            guard filteredRequirements.indices.contains(index) else { return nil }
            return RequirementSearchComboBox.displayText(for: filteredRequirements[index])
        }

        func comboBoxSelectionDidChange(_ notification: Notification) {
            guard let comboBox = notification.object as? NSComboBox else { return }
            applySelection(from: comboBox)
        }

        func controlTextDidChange(_ notification: Notification) {
            guard let comboBox = notification.object as? NSComboBox else { return }
            refreshFilter(query: comboBox.stringValue)
            comboBox.reloadData()
        }

        func controlTextDidEndEditing(_ notification: Notification) {
            guard let comboBox = notification.object as? NSComboBox else { return }
            if comboBox.indexOfSelectedItem >= 0 {
                applySelection(from: comboBox)
                return
            }

            let query = comboBox.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !query.isEmpty else { return }
            refreshFilter(query: query)
            if let exactMatch = filteredRequirements.first(where: { requirement in
                RequirementSearchComboBox.displayText(for: requirement).localizedCaseInsensitiveContains(query)
            }) {
                select(exactMatch, in: comboBox)
            }
        }

        func refreshFilter(query: String) {
            let normalizedQuery = normalize(query)
            guard !normalizedQuery.isEmpty else {
                filteredRequirements = parent.requirements
                return
            }
            filteredRequirements = parent.requirements.filter { requirement in
                let haystack = normalize([
                    requirement.title,
                    requirement.project,
                    requirement.id,
                    requirement.note ?? ""
                ].joined(separator: " "))
                return haystack.contains(normalizedQuery)
            }
        }

        private func applySelection(from comboBox: NSComboBox) {
            let index = comboBox.indexOfSelectedItem
            guard filteredRequirements.indices.contains(index) else { return }
            select(filteredRequirements[index], in: comboBox)
        }

        private func select(_ requirement: NativeRequirementOption, in comboBox: NSComboBox) {
            parent.selectedRequirementId = requirement.id
            comboBox.stringValue = RequirementSearchComboBox.displayText(for: requirement)
            parent.onSelect(requirement.id)
        }

        private func normalize(_ value: String) -> String {
            value
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .lowercased()
        }
    }
}

private final class RequirementReviewStore: ObservableObject {
    static let historyDayCount = 7

    @Published var candidates: [NativeReviewCandidate] = []
    @Published var requirements: [NativeRequirementOption] = []
    @Published var summary = NativeReviewSummary(totalCandidates: 0, pendingCandidates: 0, eventCount: 0)
    @Published var statusMessage = "准备载入"
    @Published var isLoading = false
    @Published var hasUnsavedChanges = false
    @Published var dayText: String

    private let projectRoot: String

    init(projectRoot: String) {
        self.projectRoot = projectRoot
        self.dayText = RequirementReviewStore.todayText()
    }

    var availableDayTexts: [String] {
        (0..<Self.historyDayCount).reversed().map { Self.offsetDayText(Self.todayText(), days: -$0) }
    }

    var oldestDayText: String {
        availableDayTexts.first ?? Self.todayText()
    }

    var newestDayText: String {
        availableDayTexts.last ?? Self.todayText()
    }

    var canLoadPreviousDay: Bool {
        dayText > oldestDayText
    }

    var canLoadNextDay: Bool {
        dayText < newestDayText
    }

    func reload() {
        guard !hasUnsavedChanges else {
            statusMessage = "请先保存确认结果再刷新"
            return
        }
        isLoading = true
        statusMessage = "正在读取 \(dayText) 事件..."
        let root = projectRoot
        let day = dayText
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let payload = try Self.loadPayload(projectRoot: root, dayText: day)
                DispatchQueue.main.async {
                    self.summary = payload.summary
                    self.candidates = payload.candidates
                    self.requirements = payload.requirements
                    self.statusMessage = payload.candidates.isEmpty ? "\(day) 暂无候选需求" : "已载入 \(day) 的 \(payload.candidates.count) 个候选需求"
                    self.hasUnsavedChanges = false
                    self.isLoading = false
                }
            } catch {
                DispatchQueue.main.async {
                    self.statusMessage = "载入失败：\(error.localizedDescription)"
                    self.isLoading = false
                }
            }
        }
    }

    func loadPreviousDay() {
        loadDay(Self.offsetDayText(dayText, days: -1))
    }

    func loadNextDay() {
        loadDay(Self.offsetDayText(dayText, days: 1))
    }

    func loadToday() {
        loadDay(Self.todayText())
    }

    func loadDay(_ day: String) {
        guard availableDayTexts.contains(day) else {
            statusMessage = "仅支持最近 \(Self.historyDayCount) 天"
            return
        }
        guard !hasUnsavedChanges else {
            statusMessage = "请先保存确认结果再切换日期"
            return
        }
        dayText = day
        candidates = []
        requirements = []
        summary = NativeReviewSummary(totalCandidates: 0, pendingCandidates: 0, eventCount: 0)
        reload()
    }

    func save() {
        isLoading = true
        statusMessage = "正在保存确认结果..."
        let root = projectRoot
        let day = dayText
        let decisions = candidates.map {
            NativeReviewDecision(
                candidateId: $0.candidateId,
                requirementId: $0.requirementId,
                title: $0.title,
                project: $0.project,
                requirementType: $0.requirementType,
                status: $0.status,
                eventIds: $0.eventIds,
                anchors: $0.anchors
            )
        }
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                try Self.saveDecisions(projectRoot: root, dayText: day, decisions: decisions)
                let payload = try Self.loadPayload(projectRoot: root, dayText: day)
                DispatchQueue.main.async {
                    self.summary = payload.summary
                    self.candidates = payload.candidates
                    self.requirements = payload.requirements
                    self.statusMessage = "已保存确认结果"
                    self.hasUnsavedChanges = false
                    self.isLoading = false
                }
            } catch {
                DispatchQueue.main.async {
                    self.statusMessage = "保存失败：\(error.localizedDescription)"
                    self.isLoading = false
                }
            }
        }
    }

    func generateDaily() {
        guard !hasUnsavedChanges else {
            statusMessage = "请先保存确认结果再生成日报"
            return
        }
        isLoading = true
        statusMessage = "正在生成 \(dayText) 日报..."
        let root = projectRoot
        let day = dayText
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let output = try Self.generateDaily(projectRoot: root, dayText: day)
                DispatchQueue.main.async {
                    let target = String(data: output, encoding: .utf8)?
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                        .split(whereSeparator: \.isNewline)
                        .last
                    if let target, !target.isEmpty {
                        self.statusMessage = "已生成日报：\(target)"
                    } else {
                        self.statusMessage = "已生成 \(day) 日报"
                    }
                    self.isLoading = false
                }
            } catch {
                DispatchQueue.main.async {
                    self.statusMessage = "生成失败：\(error.localizedDescription)"
                    self.isLoading = false
                }
            }
        }
    }

    func setStatus(_ id: String, status: String) {
        guard let index = candidates.firstIndex(where: { $0.id == id }) else { return }
        candidates[index].status = status
        markUnsaved()
    }

    func markUnsaved() {
        hasUnsavedChanges = true
        statusMessage = "有未保存修改"
    }

    func applyRequirementSelection(candidateId: String, requirementId: String) {
        guard let index = candidates.firstIndex(where: { $0.id == candidateId }) else { return }
        candidates[index].requirementId = requirementId
        guard !requirementId.isEmpty else {
            let suggestedTitle = candidates[index].suggestedTitle.trimmingCharacters(in: .whitespacesAndNewlines)
            if !suggestedTitle.isEmpty {
                candidates[index].title = suggestedTitle
            }
            markUnsaved()
            return
        }
        guard let requirement = requirements.first(where: { $0.id == requirementId }) else {
            markUnsaved()
            return
        }
        candidates[index].title = requirement.title
        candidates[index].project = requirement.project
        candidates[index].requirementType = requirement.requirementType
        markUnsaved()
    }

    func requirementOptions(for project: String) -> [NativeRequirementOption] {
        requirements.sorted {
            let leftSameProject = $0.project == project
            let rightSameProject = $1.project == project
            if leftSameProject != rightSameProject {
                return leftSameProject
            }
            return $0.updatedAt > $1.updatedAt
        }
    }

    private static func loadPayload(projectRoot: String, dayText: String) throws -> NativeReviewPayload {
        let script = """
        from datetime import date
        import json
        import sys
        from work_journal_agent.config import load_config
        from work_journal_agent.requirements import load_review_payload

        payload = load_review_payload(load_config(), date.fromisoformat(sys.argv[1]))
        print(json.dumps(payload, ensure_ascii=False))
        """
        let output = try runWorkJournalPython(projectRoot: projectRoot, script: script, arguments: [dayText])
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(NativeReviewPayload.self, from: output)
    }

    private static func saveDecisions(projectRoot: String, dayText: String, decisions: [NativeReviewDecision]) throws {
        let script = """
        from datetime import date
        import json
        import sys
        from work_journal_agent.config import load_config
        from work_journal_agent.requirements import save_review_decisions

        body = json.load(sys.stdin)
        saved = save_review_decisions(date.fromisoformat(sys.argv[1]), body.get("decisions", []), config=load_config())
        print(json.dumps(saved, ensure_ascii=False))
        """
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        let input = try encoder.encode(["decisions": decisions])
        _ = try runWorkJournalPython(projectRoot: projectRoot, script: script, arguments: [dayText], input: input)
    }

    private static func generateDaily(projectRoot: String, dayText: String) throws -> Data {
        let script = """
        import sys
        from work_journal_agent.cli import main

        main(["generate-daily", "--date", sys.argv[1]])
        """
        return try runWorkJournalPython(projectRoot: projectRoot, script: script, arguments: [dayText])
    }

    private static func runPython(projectRoot: String, script: String, arguments: [String], input: Data? = nil) throws -> Data {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.currentDirectoryURL = URL(fileURLWithPath: projectRoot)
        process.arguments = ["python3", "-c", script] + arguments
        var environment = ProcessInfo.processInfo.environment
        environment.merge(loadSecretsEnvironment()) { _, new in new }
        environment["PYTHONPATH"] = "\(projectRoot)/src"
        process.environment = environment

        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.standardOutput = outputPipe
        process.standardError = errorPipe
        if let input {
            let inputPipe = Pipe()
            process.standardInput = inputPipe
            try process.run()
            inputPipe.fileHandleForWriting.write(input)
            try inputPipe.fileHandleForWriting.close()
        } else {
            try process.run()
        }
        let output = outputPipe.fileHandleForReading.readDataToEndOfFile()
        let errorData = errorPipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()

        if process.terminationStatus != 0 {
            let message = String(data: errorData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
            throw NSError(domain: "WorkJournalReview", code: Int(process.terminationStatus), userInfo: [NSLocalizedDescriptionKey: message?.isEmpty == false ? message! : "python3 exited with \(process.terminationStatus)"])
        }
        return output
    }

    private static func todayText() -> String {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: Date())
    }

    private static func offsetDayText(_ value: String, days: Int) -> String {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        guard let date = formatter.date(from: value),
              let shifted = formatter.calendar.date(byAdding: .day, value: days, to: date) else {
            return todayText()
        }
        return formatter.string(from: shifted)
    }
}

private func loadSecretsEnvironment() -> [String: String] {
    guard let text = try? String(contentsOf: AppPaths.secretsPath, encoding: .utf8) else {
        return [:]
    }
    var result: [String: String] = [:]
    for rawLine in text.split(whereSeparator: \.isNewline) {
        var line = String(rawLine).trimmingCharacters(in: .whitespacesAndNewlines)
        if line.isEmpty || line.hasPrefix("#") {
            continue
        }
        if line.hasPrefix("export ") {
            line = String(line.dropFirst("export ".count)).trimmingCharacters(in: .whitespaces)
        }
        guard let equalsIndex = line.firstIndex(of: "=") else {
            continue
        }
        let key = String(line[..<equalsIndex]).trimmingCharacters(in: .whitespaces)
        var value = String(line[line.index(after: equalsIndex)...]).trimmingCharacters(in: .whitespaces)
        if value.count >= 2 {
            if value.hasPrefix("'") && value.hasSuffix("'") {
                value = String(value.dropFirst().dropLast()).replacingOccurrences(of: "'\\''", with: "'")
            } else if value.hasPrefix("\"") && value.hasSuffix("\"") {
                value = String(value.dropFirst().dropLast())
            }
        }
        if !key.isEmpty {
            result[key] = value
        }
    }
    return result
}

private func runWorkJournalPython(projectRoot: String, script: String, arguments: [String], input: Data? = nil) throws -> Data {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    process.currentDirectoryURL = URL(fileURLWithPath: projectRoot)
    process.arguments = ["python3", "-c", script] + arguments
    var environment = ProcessInfo.processInfo.environment
    environment.merge(loadSecretsEnvironment()) { _, new in new }
    environment["PYTHONPATH"] = "\(projectRoot)/src"
    process.environment = environment

    let outputPipe = Pipe()
    let errorPipe = Pipe()
    process.standardOutput = outputPipe
    process.standardError = errorPipe
    if let input {
        let inputPipe = Pipe()
        process.standardInput = inputPipe
        try process.run()
        inputPipe.fileHandleForWriting.write(input)
        try inputPipe.fileHandleForWriting.close()
    } else {
        try process.run()
    }
    let output = outputPipe.fileHandleForReading.readDataToEndOfFile()
    let errorData = errorPipe.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()

    if process.terminationStatus != 0 {
        let message = String(data: errorData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        throw NSError(domain: "WorkJournalPython", code: Int(process.terminationStatus), userInfo: [NSLocalizedDescriptionKey: message?.isEmpty == false ? message! : "python3 exited with \(process.terminationStatus)"])
    }
    return output
}

private final class RequirementManagerStore: ObservableObject {
    @Published var requirements: [NativeRequirementOption] = []
    @Published var summary = NativeRequirementManagementSummary(total: 0, active: 0, completed: 0)
    @Published var statusMessage = "准备载入"
    @Published var isLoading = false

    private let projectRoot: String

    init(projectRoot: String) {
        self.projectRoot = projectRoot
    }

    func reload() {
        isLoading = true
        statusMessage = "正在读取需求池..."
        let root = projectRoot
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let payload = try Self.loadPayload(projectRoot: root)
                DispatchQueue.main.async {
                    self.requirements = payload.requirements
                    self.summary = payload.summary
                    self.statusMessage = payload.requirements.isEmpty ? "暂无已有需求" : "已载入 \(payload.requirements.count) 个需求"
                    self.isLoading = false
                }
            } catch {
                DispatchQueue.main.async {
                    self.statusMessage = "载入失败：\(error.localizedDescription)"
                    self.isLoading = false
                }
            }
        }
    }

    func addRequirement() {
        let draft = NativeRequirementOption(
            id: "new_\(UUID().uuidString)",
            title: "",
            project: "unknown",
            requirementType: "direct",
            status: "in_progress",
            note: "",
            createdAt: "",
            updatedAt: ""
        )
        requirements.insert(draft, at: 0)
        statusMessage = "已添加未保存需求"
    }

    func save() {
        isLoading = true
        statusMessage = "正在保存需求池..."
        let root = projectRoot
        let requirementsToSave = requirements
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let payload = try Self.saveRequirements(projectRoot: root, requirements: requirementsToSave)
                DispatchQueue.main.async {
                    self.requirements = payload.requirements
                    self.summary = payload.summary
                    self.statusMessage = "已保存需求池"
                    self.isLoading = false
                }
            } catch {
                DispatchQueue.main.async {
                    self.statusMessage = "保存失败：\(error.localizedDescription)"
                    self.isLoading = false
                }
            }
        }
    }

    func mergeRequirement(sourceId: String, targetId: String) {
        let sourceId = sourceId.trimmingCharacters(in: .whitespacesAndNewlines)
        let targetId = targetId.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !sourceId.isEmpty, !targetId.isEmpty, sourceId != targetId else {
            statusMessage = "请选择不同的目标需求"
            return
        }
        isLoading = true
        statusMessage = "正在合并需求..."
        let root = projectRoot
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let payload = try Self.mergeRequirement(projectRoot: root, sourceId: sourceId, targetId: targetId)
                DispatchQueue.main.async {
                    self.requirements = payload.requirements
                    self.summary = payload.summary
                    self.statusMessage = "已合并需求"
                    self.isLoading = false
                }
            } catch {
                DispatchQueue.main.async {
                    self.statusMessage = "合并失败：\(error.localizedDescription)"
                    self.isLoading = false
                }
            }
        }
    }

    private static func loadPayload(projectRoot: String) throws -> NativeRequirementManagementPayload {
        let script = """
        import json
        from work_journal_agent.config import load_config
        from work_journal_agent.requirements import build_requirement_management_payload

        print(json.dumps(build_requirement_management_payload(load_config()), ensure_ascii=False))
        """
        let output = try runWorkJournalPython(projectRoot: projectRoot, script: script, arguments: [])
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(NativeRequirementManagementPayload.self, from: output)
    }

    private static func saveRequirements(projectRoot: String, requirements: [NativeRequirementOption]) throws -> NativeRequirementManagementPayload {
        let script = """
        import json
        import sys
        from work_journal_agent.config import load_config
        from work_journal_agent.requirements import save_requirement_threads

        body = json.load(sys.stdin)
        saved = save_requirement_threads(body.get("requirements", []), config=load_config())
        print(json.dumps(saved, ensure_ascii=False))
        """
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        let input = try encoder.encode(["requirements": requirements])
        let output = try runWorkJournalPython(projectRoot: projectRoot, script: script, arguments: [], input: input)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(NativeRequirementManagementPayload.self, from: output)
    }

    private static func mergeRequirement(projectRoot: String, sourceId: String, targetId: String) throws -> NativeRequirementManagementPayload {
        let script = """
        import json
        import sys
        from work_journal_agent.config import load_config
        from work_journal_agent.requirements import merge_requirement_threads

        body = json.load(sys.stdin)
        merged = merge_requirement_threads(body.get("target_id", ""), [body.get("source_id", "")], config=load_config())
        print(json.dumps(merged, ensure_ascii=False))
        """
        let input = try JSONSerialization.data(withJSONObject: ["source_id": sourceId, "target_id": targetId])
        let output = try runWorkJournalPython(projectRoot: projectRoot, script: script, arguments: [], input: input)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(NativeRequirementManagementPayload.self, from: output)
    }
}

private struct RequirementManagerView: View {
    @StateObject private var store: RequirementManagerStore
    @State private var filter = "active"
    @State private var mergeTargets: [String: String] = [:]

    init(projectRoot: String) {
        _store = StateObject(wrappedValue: RequirementManagerStore(projectRoot: projectRoot))
    }

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider().overlay(Theme.border)
            VStack(spacing: 0) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        header
                        summaryStrip
                        requirementList
                    }
                    .padding(.horizontal, 28)
                    .padding(.top, 26)
                    .padding(.bottom, 22)
                }
                footer
            }
            .background(Theme.surface)
        }
        .background(Theme.surface)
        .foregroundStyle(Theme.primaryText)
        .onAppear {
            if store.requirements.isEmpty {
                store.reload()
            }
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Work Journal")
                    .font(.system(size: 18, weight: .bold))
                Text("需求管理")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Theme.secondaryText)
            }
            .padding(.top, 54)
            .padding(.horizontal, 22)

            VStack(spacing: 8) {
                filterButton("active", title: "进行中", symbol: "bolt.circle", count: activeCount)
                filterButton("paused", title: "暂停中", symbol: "pause.circle", count: store.requirements.filter { $0.status == "paused" }.count)
                filterButton("completed", title: "已完结", symbol: "checkmark.seal", count: store.requirements.filter { $0.status == "completed" }.count)
                filterButton("all", title: "全部需求", symbol: "tray.full", count: store.requirements.count)
            }
            .padding(.top, 42)
            .padding(.horizontal, 14)

            Spacer()
            HStack(spacing: 8) {
                Circle().fill(store.isLoading ? Theme.orange : Theme.green).frame(width: 8, height: 8)
                Text("\(store.summary.total) 个需求")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Theme.secondaryText)
            }
            .padding(.horizontal, 22)
            .padding(.bottom, 22)
        }
        .frame(width: 188)
        .background(Theme.sidebar)
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 8) {
                Text("已有需求管理")
                    .font(.system(size: 26, weight: .bold))
                Text("维护跨天需求的名称、项目、类型和状态；完结后不再出现在需求确认下拉框。")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(Theme.secondaryText)
            }
            Spacer()
            Button {
                store.addRequirement()
            } label: {
                Label("新增需求", systemImage: "plus")
            }
            .buttonStyle(.borderedProminent)
            .tint(Theme.blue)
            Button {
                store.reload()
            } label: {
                Label("刷新", systemImage: "arrow.clockwise")
            }
            .buttonStyle(.bordered)
        }
    }

    private var summaryStrip: some View {
        HStack(spacing: 0) {
            summaryTile(symbol: "bolt.circle", title: "可选择", value: "\(activeCount)", color: Theme.green)
            separator
            summaryTile(symbol: "pause.circle", title: "暂停", value: "\(store.requirements.filter { $0.status == "paused" }.count)", color: Theme.orange)
            separator
            summaryTile(symbol: "checkmark.seal", title: "已完结", value: "\(store.summary.completed)", color: Theme.green)
            separator
            summaryTile(symbol: "tray.full", title: "全部", value: "\(store.summary.total)", color: Theme.blue)
        }
        .frame(maxWidth: .infinity, minHeight: 70)
        .padding(.horizontal, 18)
        .background(cardBackground)
    }

    private var requirementList: some View {
        VStack(spacing: 12) {
            if visibleRequirements.isEmpty {
                emptyState
            } else {
                ForEach($store.requirements) { $requirement in
                    if shouldShow(requirement) {
                        requirementCard(requirement: $requirement)
                    }
                }
            }
        }
    }

    private var footer: some View {
        HStack(spacing: 12) {
            HStack(spacing: 8) {
                Circle().fill(statusColor).frame(width: 8, height: 8)
                Text(store.statusMessage)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(statusColor)
                    .lineLimit(1)
            }
            Spacer()
            Button {
                store.reload()
            } label: {
                Label("刷新", systemImage: "arrow.clockwise")
            }
            .buttonStyle(.bordered)
            Button {
                store.save()
            } label: {
                Label("保存需求", systemImage: "square.and.arrow.down")
            }
            .buttonStyle(.borderedProminent)
            .tint(Theme.blue)
            .disabled(store.requirements.isEmpty)
        }
        .padding(.horizontal, 28)
        .frame(height: 72)
        .background(Theme.footer)
        .overlay(Rectangle().fill(Theme.border).frame(height: 1), alignment: .top)
    }

    private var activeCount: Int {
        store.requirements.filter { $0.status == "in_progress" }.count
    }

    private var visibleRequirements: [NativeRequirementOption] {
        store.requirements.filter(shouldShow)
    }

    private var statusColor: SwiftUI.Color {
        if store.statusMessage.contains("失败") { return Theme.red }
        if store.statusMessage.contains("未保存") || store.statusMessage.contains("正在") { return Theme.orange }
        return Theme.green
    }

    private var emptyState: some View {
        VStack(spacing: 10) {
            Image(systemName: "tray")
                .font(.system(size: 32, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
            Text(store.isLoading ? "正在加载..." : "当前筛选下没有需求")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
        }
        .frame(maxWidth: .infinity, minHeight: 220)
        .background(cardBackground)
    }

    private var separator: some View {
        Rectangle().fill(Theme.border).frame(width: 1, height: 42).padding(.horizontal, 18)
    }

    private var cardBackground: some View {
        RoundedRectangle(cornerRadius: 8)
            .fill(Theme.card)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border))
    }

    private var fieldBackground: some View {
        RoundedRectangle(cornerRadius: 6)
            .fill(Theme.field)
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.border))
    }

    private func requirementCard(requirement: Binding<NativeRequirementOption>) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                fieldColumn(title: "需求标题", width: nil) {
                    TextField("", text: requirement.title)
                        .textFieldStyle(.plain)
                        .font(.system(size: 16, weight: .bold))
                        .padding(.horizontal, 10)
                        .frame(height: 38)
                        .background(fieldBackground)
                }
                fieldColumn(title: "涉及项目", width: 180) {
                    HStack(spacing: 6) {
                        Image(systemName: "folder")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(Theme.mutedText)
                        Text(requirement.wrappedValue.project.isEmpty ? "自动关联" : requirement.wrappedValue.project)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(requirement.wrappedValue.project.isEmpty || requirement.wrappedValue.project == "unknown" ? Theme.mutedText : Theme.secondaryText)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                    .padding(.horizontal, 10)
                    .frame(width: 180, height: 38, alignment: .leading)
                    .background(fieldBackground)
                }
                fieldColumn(title: "类型", width: 118) {
                    Picker("", selection: requirement.requirementType) {
                        Text("直接").tag("direct")
                        Text("方案").tag("plan-driven")
                        Text("Review").tag("review")
                        Text("排障").tag("debug")
                        Text("文档").tag("docs")
                    }
                    .frame(width: 118, height: 38)
                }
                fieldColumn(title: "状态", width: 142) {
                    Picker("", selection: requirement.status) {
                        Text("进行中").tag("in_progress")
                        Text("暂停").tag("paused")
                        Text("已完结").tag("completed")
                        Text("归档").tag("archived")
                    }
                    .frame(width: 142, height: 38)
                }
            }

            HStack(spacing: 8) {
                statusBadge(requirement.wrappedValue.id.hasPrefix("new_") ? "未保存" : requirement.wrappedValue.id, color: Theme.mutedText)
                statusBadge(statusTitle(requirement.wrappedValue.status), color: statusColor(requirement.wrappedValue.status))
                if !requirement.wrappedValue.createdAt.isEmpty {
                    statusBadge("创建 \(shortDate(requirement.wrappedValue.createdAt))", color: Theme.secondaryText)
                }
                if !requirement.wrappedValue.updatedAt.isEmpty {
                    statusBadge("更新 \(shortDate(requirement.wrappedValue.updatedAt))", color: Theme.secondaryText)
                }
                Spacer()
            }

            VStack(alignment: .leading, spacing: 7) {
                Text("备注")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.secondaryText)
                TextEditor(text: noteBinding(requirement))
                    .font(.system(size: 13, weight: .medium))
                    .scrollContentBackground(.hidden)
                    .frame(minHeight: 56, maxHeight: 78)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 6)
                    .background(fieldBackground)
            }

            if !requirement.wrappedValue.id.hasPrefix("new_") {
                mergeRequirementSection(requirement: requirement)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Theme.card)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(statusBorder(requirement.wrappedValue.status)))
        )
    }

    private func mergeRequirementSection(requirement: Binding<NativeRequirementOption>) -> some View {
        let sourceId = requirement.wrappedValue.id
        let candidates = store.requirements.filter { !$0.id.hasPrefix("new_") && $0.id != sourceId }
        return HStack(alignment: .bottom, spacing: 10) {
            VStack(alignment: .leading, spacing: 7) {
                Text("合并需求")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.secondaryText)
                RequirementSearchComboBox(
                    requirements: candidates,
                    selectedRequirementId: mergeTargetBinding(for: sourceId),
                    placeholder: "输入目标需求标题查询",
                    onSelect: { selectedRequirementId in
                        mergeTargets[sourceId] = selectedRequirementId
                    }
                )
                .frame(height: 36)
                .background(fieldBackground)
            }
            Button {
                store.mergeRequirement(sourceId: sourceId, targetId: mergeTargets[sourceId] ?? "")
            } label: {
                Label("合并到目标", systemImage: "arrow.triangle.merge")
            }
            .buttonStyle(.bordered)
            .disabled((mergeTargets[sourceId] ?? "").isEmpty || store.isLoading)
        }
    }

    private func mergeTargetBinding(for sourceId: String) -> Binding<String> {
        Binding(
            get: { mergeTargets[sourceId] ?? "" },
            set: { mergeTargets[sourceId] = $0 }
        )
    }

    private func fieldColumn<Content: View>(title: String, width: CGFloat?, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
            content()
        }
        .frame(width: width, alignment: .leading)
    }

    private func filterButton(_ id: String, title: String, symbol: String, count: Int) -> some View {
        Button {
            filter = id
        } label: {
            HStack(spacing: 12) {
                Image(systemName: symbol)
                    .font(.system(size: 15, weight: .semibold))
                    .frame(width: 22)
                Text(title)
                    .font(.system(size: 14, weight: .semibold))
                Spacer()
                Text("\(count)")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(Theme.secondaryText)
            }
            .foregroundStyle(filter == id ? Theme.primaryText : Theme.secondaryText)
            .padding(.horizontal, 14)
            .frame(height: 42)
            .background(filter == id ? Theme.selected : SwiftUI.Color.clear)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
    }

    private func summaryTile(symbol: String, title: String, value: String, color: SwiftUI.Color) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 7) {
                    Text(title).font(.system(size: 13, weight: .semibold))
                    Circle().fill(color).frame(width: 8, height: 8)
                }
                Text(value)
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(Theme.primaryText)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity)
    }

    private func statusBadge(_ text: String, color: SwiftUI.Color) -> some View {
        Text(text.isEmpty ? "unknown" : text)
            .font(.system(size: 12, weight: .bold))
            .foregroundStyle(color)
            .padding(.horizontal, 10)
            .frame(height: 26)
            .background(RoundedRectangle(cornerRadius: 13).fill(Theme.badge))
    }

    private func noteBinding(_ requirement: Binding<NativeRequirementOption>) -> Binding<String> {
        Binding(
            get: { requirement.wrappedValue.note ?? "" },
            set: { requirement.wrappedValue.note = $0 }
        )
    }

    private func shouldShow(_ requirement: NativeRequirementOption) -> Bool {
        switch filter {
        case "active":
            return requirement.status == "in_progress"
        case "paused":
            return requirement.status == "paused"
        case "completed":
            return requirement.status == "completed"
        default:
            return true
        }
    }

    private func statusTitle(_ status: String) -> String {
        switch status {
        case "in_progress": return "进行中"
        case "paused": return "暂停中"
        case "completed": return "已完结"
        case "archived": return "归档"
        default: return status
        }
    }

    private func statusColor(_ status: String) -> SwiftUI.Color {
        switch status {
        case "in_progress": return Theme.green
        case "paused": return Theme.orange
        case "completed": return Theme.green
        case "archived": return Theme.mutedText
        default: return Theme.secondaryText
        }
    }

    private func statusBorder(_ status: String) -> SwiftUI.Color {
        switch status {
        case "in_progress": return Theme.green
        case "paused": return Theme.orange
        case "completed": return Theme.border
        default: return Theme.border
        }
    }

    private func shortDate(_ value: String) -> String {
        String(value.prefix(10))
    }
}

private struct RequirementReviewView: View {
    @StateObject private var store: RequirementReviewStore
    @State private var filter = "all"
    @State private var intakeModes: [String: String] = [:]

    init(projectRoot: String) {
        _store = StateObject(wrappedValue: RequirementReviewStore(projectRoot: projectRoot))
    }

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider().overlay(Theme.border)
            VStack(spacing: 0) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        header
                        summaryStrip
                        candidateList
                    }
                    .padding(.horizontal, 28)
                    .padding(.top, 26)
                    .padding(.bottom, 22)
                }
                footer
            }
            .background(Theme.surface)
        }
        .background(Theme.surface)
        .foregroundStyle(Theme.primaryText)
        .onAppear {
            if store.candidates.isEmpty {
                store.reload()
            }
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Work Journal")
                    .font(.system(size: 18, weight: .bold))
                Text("最近 7 天")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Theme.secondaryText)
            }
            .padding(.top, 54)
            .padding(.horizontal, 22)

            VStack(spacing: 8) {
                filterButton("all", title: "全部候选", symbol: "tray.full", count: store.candidates.count)
                filterButton("pending", title: "待确认", symbol: "clock", count: store.candidates.filter { $0.status != "confirmed" && $0.status != "ignored" }.count)
                filterButton("confirmed", title: "已确认", symbol: "checkmark.circle", count: store.candidates.filter { $0.status == "confirmed" }.count)
                filterButton("ignored", title: "已忽略", symbol: "xmark.circle", count: store.candidates.filter { $0.status == "ignored" }.count)
            }
            .padding(.top, 42)
            .padding(.horizontal, 14)

            VStack(alignment: .leading, spacing: 8) {
                Text("历史日期")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.mutedText)
                    .padding(.horizontal, 8)
                ForEach(store.availableDayTexts, id: \.self) { day in
                    dayButton(day)
                }
            }
            .padding(.top, 26)
            .padding(.horizontal, 14)

            Spacer()
            HStack(spacing: 8) {
                Circle().fill(store.isLoading ? Theme.orange : Theme.green).frame(width: 8, height: 8)
                Text(store.dayText)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Theme.secondaryText)
            }
            .padding(.horizontal, 22)
            .padding(.bottom, 22)
        }
        .frame(width: 188)
        .background(Theme.sidebar)
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 8) {
                Text("历史需求确认")
                    .font(.system(size: 26, weight: .bold))
                Text("最近 7 天内可补确认、补生成日报；日报仍按事件发生日期归档。")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(Theme.secondaryText)
                Text("日期：\(store.dayText)")
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(Theme.secondaryText)
            }
            Spacer()
            HStack(spacing: 8) {
                Button {
                    store.loadPreviousDay()
                } label: {
                    Label("前一天", systemImage: "chevron.left")
                }
                .buttonStyle(.bordered)
                .disabled(!store.canLoadPreviousDay || store.isLoading)

                Button {
                    store.loadToday()
                } label: {
                    Label("今天", systemImage: "calendar")
                }
                .buttonStyle(.bordered)
                .disabled(store.dayText == store.newestDayText || store.isLoading)

                Button {
                    store.loadNextDay()
                } label: {
                    Label("后一天", systemImage: "chevron.right")
                }
                .buttonStyle(.bordered)
                .disabled(!store.canLoadNextDay || store.isLoading)
            }
            Button {
                store.reload()
            } label: {
                Label("刷新", systemImage: "arrow.clockwise")
            }
            .buttonStyle(.bordered)
        }
    }

    private var summaryStrip: some View {
        HStack(spacing: 0) {
            summaryTile(symbol: "rectangle.stack", title: "候选需求", value: "\(store.summary.totalCandidates)", color: Theme.blue)
            separator
            summaryTile(symbol: "clock", title: "待确认", value: "\(store.summary.pendingCandidates)", color: store.summary.pendingCandidates == 0 ? Theme.green : Theme.orange)
            separator
            summaryTile(symbol: "point.3.connected.trianglepath.dotted", title: "事件数", value: "\(store.summary.eventCount)", color: Theme.green)
            separator
            summaryTile(symbol: "checkmark.seal", title: "已确认", value: "\(store.candidates.filter { $0.status == "confirmed" }.count)", color: Theme.green)
        }
        .frame(maxWidth: .infinity, minHeight: 70)
        .padding(.horizontal, 18)
        .background(cardBackground)
    }

    private var candidateList: some View {
        VStack(spacing: 12) {
            if visibleCandidates.isEmpty {
                emptyState
            } else {
                ForEach($store.candidates) { $candidate in
                    if shouldShow(candidate) {
                        candidateCard(candidate: $candidate)
                    }
                }
            }
        }
    }

    private var visibleCandidates: [NativeReviewCandidate] {
        store.candidates.filter(shouldShow)
    }

    private func dayButton(_ day: String) -> some View {
        let isSelected = store.dayText == day
        return Button {
            store.loadDay(day)
        } label: {
            HStack(spacing: 10) {
                Image(systemName: isSelected ? "calendar.circle.fill" : "calendar")
                    .font(.system(size: 14, weight: .semibold))
                    .frame(width: 20)
                Text(day)
                    .font(.system(size: 13, weight: .semibold, design: .monospaced))
                Spacer(minLength: 0)
            }
            .foregroundStyle(isSelected ? Theme.primaryText : Theme.secondaryText)
            .padding(.horizontal, 12)
            .frame(height: 36)
            .background(isSelected ? Theme.selected : SwiftUI.Color.clear)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .disabled(store.isLoading)
    }

    private var emptyState: some View {
        VStack(spacing: 10) {
            Image(systemName: "checkmark.seal")
                .font(.system(size: 32, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
            Text(store.isLoading ? "正在加载..." : "当前筛选下没有候选需求")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
        }
        .frame(maxWidth: .infinity, minHeight: 220)
        .background(cardBackground)
    }

    private var footer: some View {
        HStack(spacing: 12) {
            HStack(spacing: 8) {
                Circle().fill(statusColor).frame(width: 8, height: 8)
                Text(store.statusMessage)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(statusColor)
                    .lineLimit(1)
            }
            Spacer()
            Button {
                store.reload()
            } label: {
                Label("刷新", systemImage: "arrow.clockwise")
            }
            .buttonStyle(.bordered)
            Button {
                store.generateDaily()
            } label: {
                Label("生成该日日报", systemImage: "doc.text")
            }
            .buttonStyle(.bordered)
            .disabled(store.isLoading)
            Button {
                store.save()
            } label: {
                Label("保存确认", systemImage: "square.and.arrow.down")
            }
            .buttonStyle(.borderedProminent)
            .tint(Theme.blue)
            .disabled(store.candidates.isEmpty)
        }
        .padding(.horizontal, 28)
        .frame(height: 72)
        .background(Theme.footer)
        .overlay(Rectangle().fill(Theme.border).frame(height: 1), alignment: .top)
    }

    private var statusColor: SwiftUI.Color {
        if store.statusMessage.contains("失败") { return Theme.red }
        if store.statusMessage.contains("未保存") || store.statusMessage.contains("正在") { return Theme.orange }
        return Theme.green
    }

    private var separator: some View {
        Rectangle().fill(Theme.border).frame(width: 1, height: 42).padding(.horizontal, 18)
    }

    private var cardBackground: some View {
        RoundedRectangle(cornerRadius: 8)
            .fill(Theme.card)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border))
    }

    private func filterButton(_ id: String, title: String, symbol: String, count: Int) -> some View {
        Button {
            filter = id
        } label: {
            HStack(spacing: 12) {
                Image(systemName: symbol)
                    .font(.system(size: 15, weight: .semibold))
                    .frame(width: 22)
                Text(title)
                    .font(.system(size: 14, weight: .semibold))
                Spacer()
                Text("\(count)")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(Theme.secondaryText)
            }
            .foregroundStyle(filter == id ? Theme.primaryText : Theme.secondaryText)
            .padding(.horizontal, 14)
            .frame(height: 42)
            .background(filter == id ? Theme.selected : SwiftUI.Color.clear)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
    }

    private func summaryTile(symbol: String, title: String, value: String, color: SwiftUI.Color) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 7) {
                    Text(title).font(.system(size: 13, weight: .semibold))
                    Circle().fill(color).frame(width: 8, height: 8)
                }
                Text(value)
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(Theme.primaryText)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity)
    }

    private func candidateCard(candidate: Binding<NativeReviewCandidate>) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 20) {
                intakeSection(candidate: candidate)
                    .frame(maxWidth: .infinity, alignment: .leading)

                reviewControlSection(candidate: candidate)
                    .frame(width: 218, alignment: .leading)
            }

            HStack(spacing: 8) {
                statusBadge(candidate.wrappedValue.project, color: Theme.blue)
                statusBadge("置信度 \(String(format: "%.2f", candidate.wrappedValue.confidence))", color: confidenceColor(candidate.wrappedValue.confidence))
                statusBadge("事件 \(candidate.wrappedValue.eventCount)", color: Theme.green)
                statusBadge(candidate.wrappedValue.sources.joined(separator: ", "), color: Theme.mutedText)
                Spacer()
            }

            HStack(alignment: .top, spacing: 12) {
                textBlock(title: "需求线索", text: candidate.wrappedValue.request)
                textBlock(title: "结论/动作", text: candidate.wrappedValue.decision)
            }

            if !candidate.wrappedValue.reasons.isEmpty {
                compactList(title: "提示", values: candidate.wrappedValue.reasons, color: Theme.orange)
            }
            if !candidate.wrappedValue.files.isEmpty {
                compactList(title: "相关文件", values: candidate.wrappedValue.files, color: Theme.secondaryText)
            }
            anchorSection(candidate.wrappedValue.anchors)

            HStack {
                Spacer()
                Button("确认") { store.setStatus(candidate.wrappedValue.id, status: "confirmed") }
                Button("待确认") { store.setStatus(candidate.wrappedValue.id, status: "pending") }
                Button("忽略") { store.setStatus(candidate.wrappedValue.id, status: "ignored") }
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Theme.card)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(borderColor(for: candidate.wrappedValue.status)))
        )
    }

    private func intakeSection(candidate: Binding<NativeReviewCandidate>) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("需求入库")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Theme.secondaryText)
                    Text(intakeMode(for: candidate.wrappedValue) == "existing" ? "归并到已有需求标题" : "创建新的需求标题")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Theme.mutedText)
                }
                Spacer(minLength: 0)
                HStack(spacing: 6) {
                    intakeModeButton(candidate: candidate, mode: "new", title: "新建需求", symbol: "plus.square")
                    intakeModeButton(candidate: candidate, mode: "existing", title: "归并已有", symbol: "arrow.triangle.merge")
                }
            }

            if intakeMode(for: candidate.wrappedValue) == "existing" {
                existingRequirementSection(candidate: candidate)
            } else {
                newRequirementSection(candidate: candidate)
            }
        }
    }

    private func intakeModeButton(candidate: Binding<NativeReviewCandidate>, mode: String, title: String, symbol: String) -> some View {
        let isSelected = intakeMode(for: candidate.wrappedValue) == mode
        return Button {
            intakeModes[candidate.wrappedValue.id] = mode
            if mode == "new" {
                store.applyRequirementSelection(candidateId: candidate.wrappedValue.id, requirementId: "")
            }
        } label: {
            Label(title, systemImage: symbol)
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(isSelected ? Theme.primaryText : Theme.secondaryText)
                .padding(.horizontal, 11)
                .frame(height: 30)
                .background(
                    RoundedRectangle(cornerRadius: 7)
                        .fill(isSelected ? Theme.blue : Theme.badge)
                        .overlay(RoundedRectangle(cornerRadius: 7).stroke(isSelected ? Theme.blue : Theme.border))
                )
        }
        .buttonStyle(.plain)
    }

    private func newRequirementSection(candidate: Binding<NativeReviewCandidate>) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("新建需求标题")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
            TextField("", text: candidate.title)
                .textFieldStyle(.plain)
                .font(.system(size: 16, weight: .bold))
                .padding(.horizontal, 10)
                .frame(height: 40)
                .frame(maxWidth: .infinity)
                .background(fieldBackground)
                .onChange(of: candidate.wrappedValue.title) { _, _ in
                    store.markUnsaved()
                }
            aiSuggestionText(candidate.wrappedValue.suggestedTitle)
        }
    }

    private func existingRequirementSection(candidate: Binding<NativeReviewCandidate>) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("已有需求")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
            RequirementSearchComboBox(
                requirements: store.requirementOptions(for: candidate.wrappedValue.project),
                selectedRequirementId: candidate.requirementId,
                placeholder: "输入标题、项目或需求 ID 查询",
                onSelect: { selectedRequirementId in
                    intakeModes[candidate.wrappedValue.id] = "existing"
                    store.applyRequirementSelection(candidateId: candidate.wrappedValue.id, requirementId: selectedRequirementId)
                }
            )
            .frame(height: 40)
            .background(fieldBackground)

            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Image(systemName: candidate.wrappedValue.requirementId.isEmpty ? "magnifyingglass" : "link")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(candidate.wrappedValue.requirementId.isEmpty ? Theme.mutedText : Theme.green)
                if candidate.wrappedValue.requirementId.isEmpty {
                    Text("未选择已有需求，当前 AI 标题保留为归并线索。")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Theme.mutedText)
                        .lineLimit(2)
                } else {
                    Text("归并后标题：\(candidate.wrappedValue.title)")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Theme.secondaryText)
                        .lineLimit(2)
                }
            }
            aiSuggestionText(candidate.wrappedValue.suggestedTitle)
        }
    }

    private func reviewControlSection(candidate: Binding<NativeReviewCandidate>) -> some View {
        VStack(alignment: .leading, spacing: 11) {
            VStack(alignment: .leading, spacing: 7) {
                Text("状态")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.secondaryText)
                Picker("", selection: candidate.status) {
                    Text("确认").tag("confirmed")
                    Text("待确认").tag("pending")
                    Text("忽略").tag("ignored")
                }
                .pickerStyle(.segmented)
                .frame(width: 210, height: 38)
                .onChange(of: candidate.wrappedValue.status) { _, _ in
                    store.markUnsaved()
                }
            }
            HStack(alignment: .center, spacing: 10) {
                VStack(alignment: .leading, spacing: 7) {
                    Text("类型")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Theme.secondaryText)
                    Picker("", selection: candidate.requirementType) {
                        Text("直接").tag("direct")
                        Text("方案").tag("plan-driven")
                        Text("Review").tag("review")
                        Text("排障").tag("debug")
                        Text("文档").tag("docs")
                    }
                    .frame(width: 118, height: 38)
                    .onChange(of: candidate.wrappedValue.requirementType) { _, _ in
                        store.markUnsaved()
                    }
                }
            }
        }
    }

    private func intakeModeBinding(_ candidate: Binding<NativeReviewCandidate>) -> Binding<String> {
        Binding(
            get: { intakeMode(for: candidate.wrappedValue) },
            set: { mode in
                intakeModes[candidate.wrappedValue.id] = mode
                if mode == "new" {
                    store.applyRequirementSelection(candidateId: candidate.wrappedValue.id, requirementId: "")
                }
            }
        )
    }

    private func intakeMode(for candidate: NativeReviewCandidate) -> String {
        if let mode = intakeModes[candidate.id] {
            return mode
        }
        return candidate.requirementId.isEmpty ? "new" : "existing"
    }

    private func aiSuggestionText(_ title: String) -> some View {
        Text("AI 建议标题：\(title.isEmpty ? "暂无" : title)")
            .font(.system(size: 12, weight: .medium))
            .foregroundStyle(Theme.mutedText)
            .lineLimit(2)
            .truncationMode(.tail)
    }

    private func textBlock(title: String, text: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
            Text(text.isEmpty ? "暂无" : text)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(text.isEmpty ? Theme.mutedText : Theme.primaryText)
                .lineLimit(3)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(10)
                .background(fieldBackground)
        }
    }

    private func compactList(title: String, values: [String], color: SwiftUI.Color) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(color)
            ForEach(values.prefix(5), id: \.self) { value in
                HStack(alignment: .top, spacing: 7) {
                    Circle().fill(color).frame(width: 5, height: 5).padding(.top, 7)
                    Text(value)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Theme.secondaryText)
                        .lineLimit(2)
                        .truncationMode(.middle)
                }
            }
        }
    }

    private func anchorSection(_ anchors: [String: [String]]) -> some View {
        let visible = anchors.filter { !$0.value.isEmpty }.sorted { anchorTitle($0.key) < anchorTitle($1.key) }
        return VStack(alignment: .leading, spacing: 6) {
            ForEach(visible, id: \.key) { key, values in
                compactList(title: anchorTitle(key), values: values, color: Theme.secondaryText)
            }
        }
    }

    private func shouldShow(_ candidate: NativeReviewCandidate) -> Bool {
        switch filter {
        case "pending":
            return candidate.status != "confirmed" && candidate.status != "ignored"
        case "confirmed":
            return candidate.status == "confirmed"
        case "ignored":
            return candidate.status == "ignored"
        default:
            return true
        }
    }

    private func borderColor(for status: String) -> SwiftUI.Color {
        switch status {
        case "confirmed": return Theme.green
        case "ignored": return Theme.mutedText
        default: return Theme.border
        }
    }

    private func confidenceColor(_ confidence: Double) -> SwiftUI.Color {
        confidence >= 0.85 ? Theme.green : (confidence >= 0.65 ? Theme.orange : Theme.red)
    }

    private var fieldBackground: some View {
        RoundedRectangle(cornerRadius: 6)
            .fill(Theme.field)
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.border))
    }

    private func statusBadge(_ text: String, color: SwiftUI.Color) -> some View {
        Text(text.isEmpty ? "unknown" : text)
            .font(.system(size: 12, weight: .bold))
            .foregroundStyle(color)
            .padding(.horizontal, 10)
            .frame(height: 26)
            .background(RoundedRectangle(cornerRadius: 13).fill(Theme.badge))
    }

    private func anchorTitle(_ key: String) -> String {
        switch key {
        case "implementation_files": return "实现文件"
        case "plan_docs": return "方案文档"
        case "review_docs": return "Review/审查"
        case "requirement_docs": return "需求/技术文档"
        default: return key
        }
    }
}

private struct WorkJournalSettingsView: View {
    private let projectRoot: String
    @State private var draft = WorkJournalConfig.load()
    @State private var apiKey = ""
    @State private var selectedSection = "overview"
    @State private var statusMessage = ""

    init(projectRoot: String) {
        self.projectRoot = projectRoot
    }

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider().overlay(Theme.border)
            VStack(spacing: 0) {
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 14) {
                            header.id("overview")
                            overviewStrip
                            obsidianCard.id("obsidian")
                            sourcesCard.id("sources")
                            aiCard.id("ai")
                            pathsCard.id("paths")
                            logsCard.id("logs")
                        }
                        .padding(.horizontal, 28)
                        .padding(.top, 26)
                        .padding(.bottom, 22)
                    }
                    .onChange(of: selectedSection) { _, value in
                        withAnimation(.easeInOut(duration: 0.18)) {
                            proxy.scrollTo(value, anchor: .top)
                        }
                    }
                }
                footer
            }
            .background(Theme.surface)
        }
        .background(Theme.surface)
        .foregroundStyle(Theme.primaryText)
        .onAppear {
            draft = WorkJournalConfig.load()
            statusMessage = configStatusText
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Work Journal")
                    .font(.system(size: 18, weight: .bold))
                    .foregroundStyle(Theme.primaryText)
                Text("本机设置")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Theme.secondaryText)
            }
            .padding(.top, 54)
            .padding(.horizontal, 22)

            VStack(spacing: 8) {
                sidebarItem("overview", "总览", "square.grid.2x2")
                sidebarItem("obsidian", "Obsidian", "book.closed")
                sidebarItem("sources", "数据源", "externaldrive.connected.to.line.below")
                sidebarItem("ai", "AI", "sparkles")
                sidebarItem("paths", "路径", "folder")
                sidebarItem("logs", "日志", "doc.text.magnifyingglass")
            }
            .padding(.top, 42)
            .padding(.horizontal, 14)

            Spacer()
            HStack(spacing: 8) {
                Circle().fill(Theme.green).frame(width: 8, height: 8)
                Text("本地模式")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Theme.secondaryText)
            }
            .padding(.horizontal, 22)
            .padding(.bottom, 22)
        }
        .frame(width: 188)
        .background(Theme.sidebar)
    }

    private func sidebarItem(_ id: String, _ title: String, _ symbol: String) -> some View {
        Button {
            selectedSection = id
        } label: {
            HStack(spacing: 12) {
                Image(systemName: symbol)
                    .font(.system(size: 16, weight: .semibold))
                    .frame(width: 22)
                Text(title)
                    .font(.system(size: 15, weight: .semibold))
                Spacer()
            }
            .foregroundStyle(selectedSection == id ? Theme.primaryText : Theme.secondaryText)
            .padding(.horizontal, 14)
            .frame(height: 42)
            .background(selectedSection == id ? Theme.selected : SwiftUI.Color.clear)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("本机配置")
                .font(.system(size: 26, weight: .bold))
            Text("管理本机配置、数据源、AI 与 Obsidian 同步设置，配置只保存在本机。")
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(Theme.secondaryText)
            HStack(spacing: 8) {
                Text("配置文件：")
                    .foregroundStyle(Theme.secondaryText)
                Text("~/.config/work-journal-agent/config.toml")
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(Theme.secondaryText)
                Button {
                    NSWorkspace.shared.open(AppPaths.configPath.deletingLastPathComponent())
                } label: {
                    Image(systemName: "doc.on.doc")
                }
                .buttonStyle(.borderless)
                .foregroundStyle(Theme.secondaryText)
            }
            .font(.system(size: 13, weight: .medium))
        }
    }

    private var overviewStrip: some View {
        HStack(spacing: 0) {
            overviewTile(symbol: "doc", title: "配置文件", value: FileManager.default.fileExists(atPath: AppPaths.configPath.path) ? "已创建" : "未创建", color: FileManager.default.fileExists(atPath: AppPaths.configPath.path) ? Theme.green : Theme.orange)
            separator
            overviewTile(symbol: "book.closed", title: "Obsidian", value: draft.vaultPath.isEmpty ? "未配置" : "已配置", color: draft.vaultPath.isEmpty ? Theme.orange : Theme.green)
            separator
            overviewTile(symbol: "sparkles", title: "AI", value: draft.aiEnabled ? draft.provider : "关闭", color: draft.aiEnabled ? Theme.blue : Theme.mutedText)
            separator
            overviewTile(symbol: "arrow.triangle.2.circlepath", title: "自动同步", value: FileManager.default.fileExists(atPath: AppPaths.launchAgentPath.path) ? "已安装" : "未安装", color: Theme.mutedText)
        }
        .frame(maxWidth: .infinity, minHeight: 70)
        .padding(.horizontal, 18)
        .background(cardBackground)
    }

    private var obsidianCard: some View {
        SettingsCard(title: "Obsidian 设置", subtitle: "设置日报写入位置和笔记目录。") {
            fieldRow("Obsidian Vault 路径", text: $draft.vaultPath, chooser: .vault, status: draft.vaultPath.isEmpty ? "未配置" : pathStatus(draft.vaultPath, directory: true))
            fieldRow("Daily 目录", text: $draft.dailyDir)
            fieldRow("Tasks 目录", text: $draft.taskDir)
            fieldRow("Knowledge 目录", text: $draft.knowledgeDir)
            toggleRow("写入 Knowledge 笔记", detail: "为知识沉淀生成独立专题笔记。", isOn: $draft.writeKnowledgeNotes)
        }
    }

    private var sourcesCard: some View {
        SettingsCard(title: "数据源", subtitle: "采集本机 Agent 事件源。") {
            sourceRow(title: "Codex", detail: "读取 ~/.codex/sessions 中的会话事件。", isOn: $draft.codexEnabled, text: $draft.codexSessionsRoot, chooser: .codexSessions, status: pathStatus(draft.codexSessionsRoot, directory: true))
            sourceRow(title: "Claude Code", detail: "配置 hooks 写入 Work Journal 数据库。", isOn: $draft.claudeEnabled, text: $draft.claudeSettingsPath, chooser: .claudeSettings, status: pathStatus(draft.claudeSettingsPath, directory: false))
            sourceRow(title: "OpenCode", detail: "导入 OpenCode 本地 storage 事件。", isOn: $draft.opencodeEnabled, text: $draft.opencodeStorageRoot, chooser: .opencodeStorage, status: pathStatus(draft.opencodeStorageRoot, directory: true))
            sourceRow(title: "Kun", detail: "导入 Kun threads 与项目 .kunsdd 需求文档。", isOn: $draft.kunEnabled, text: $draft.kunStorageRoot, chooser: .kunStorage, status: pathStatus(draft.kunStorageRoot, directory: true))
            sourceRow(title: "ZCode", detail: "导入 ZCode 本地 sqlite 会话与工具事件。", isOn: $draft.zcodeEnabled, text: $draft.zcodeStorageRoot, chooser: .zcodeStorage, status: pathStatus(draft.zcodeStorageRoot, directory: true))
        }
    }

    private var aiCard: some View {
        SettingsCard(title: "AI 设置", subtitle: "可选启用 DeepSeek 复核需求聚类并生成摘要。") {
            toggleRow("启用 DeepSeek", detail: "只发送压缩后的任务事件，不上传完整聊天全文。", isOn: $draft.aiEnabled)
            HStack(spacing: 14) {
                compactField("Provider", text: $draft.provider)
                compactField("Model", text: $draft.model)
                compactField("Base URL", text: $draft.baseUrl)
            }
            HStack(spacing: 14) {
                compactField("通用超时", text: $draft.timeoutSeconds, width: 130)
                compactField("聚类超时", text: $draft.clusterReviewTimeoutSeconds, width: 130)
                compactField("缓存天数", text: $draft.cacheRetentionDays, width: 130)
                Spacer()
            }
            HStack(spacing: 14) {
                secureField("API Key", text: $apiKey, placeholder: FileManager.default.fileExists(atPath: AppPaths.secretsPath.path) ? "已保存，留空保持不变" : "未保存")
                statusBadge(FileManager.default.fileExists(atPath: AppPaths.secretsPath.path) ? "已保存" : "未保存")
                Button("保存密钥") {
                    saveApiKey()
                }
                .buttonStyle(.bordered)
                .disabled(apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            HStack(spacing: 34) {
                miniToggle("启用缓存", isOn: $draft.cacheEnabled)
                miniToggle("需求聚类复核", isOn: $draft.clusterReviewEnabled)
                miniToggle("Knowledge AI", isOn: $draft.knowledgeEnabled)
            }
        }
    }

    private var pathsCard: some View {
        SettingsCard(title: "路径", subtitle: "配置 SQLite 数据库、备用输出目录、OpenCode 插件和 Kun 项目目录。") {
            fieldRow("SQLite 数据库", text: $draft.databasePath, chooser: .database)
            fieldRow("备用输出目录", text: $draft.outputDir, chooser: .output)
            fieldRow("OpenCode 插件", text: $draft.opencodePluginPath, chooser: .opencodePlugin)
            fieldRow("Kun 项目根目录", text: $draft.kunProjectRoot, chooser: .kunProjectRoot)
        }
    }

    private var logsCard: some View {
        SettingsCard(title: "日志", subtitle: "打开本机日志和运行数据，便于排查同步问题。") {
            infoRow("菜单日志", "/tmp/work-journal-agent-menubar.log")
            infoRow("LaunchAgent", AppPaths.launchAgentPath.path)
            HStack {
                Button("打开日志") {
                    touchAndOpen("/tmp/work-journal-agent-menubar.log")
                }
                Button("打开数据目录") {
                    openDirectory(AppPaths.dataDir)
                }
                Spacer()
            }
        }
    }

    private var footer: some View {
        HStack(spacing: 12) {
            HStack(spacing: 8) {
                Circle().fill(statusMessage.contains("失败") ? Theme.red : Theme.orange).frame(width: 8, height: 8)
                Text(statusMessage.isEmpty ? configStatusText : statusMessage)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(statusMessage.contains("失败") ? Theme.red : Theme.orange)
            }
            Spacer()
            Button("重新配置本机...") {
                runTerminalCommand("cd \(shellQuote(projectRoot)) && PYTHONPATH=src python3 -m work_journal_agent setup")
            }
            Button("打开配置目录") {
                openDirectory(AppPaths.configPath.deletingLastPathComponent())
            }
            Button("还原") {
                draft = WorkJournalConfig.load()
                apiKey = ""
                statusMessage = "已还原为磁盘配置"
            }
            Button("保存配置") {
                save()
            }
            .buttonStyle(.borderedProminent)
            .tint(Theme.blue)
        }
        .padding(.horizontal, 28)
        .frame(height: 72)
        .background(Theme.footer)
        .overlay(Rectangle().fill(Theme.border).frame(height: 1), alignment: .top)
    }

    private var separator: some View {
        Rectangle().fill(Theme.border).frame(width: 1, height: 42).padding(.horizontal, 18)
    }

    private var cardBackground: some View {
        RoundedRectangle(cornerRadius: 8)
            .fill(Theme.card)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border))
    }

    private func overviewTile(symbol: String, title: String, value: String, color: SwiftUI.Color) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 7) {
                    Text(title).font(.system(size: 13, weight: .semibold))
                    Circle().fill(color).frame(width: 8, height: 8)
                }
                Text(value)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Theme.secondaryText)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity)
    }

    private func fieldRow(_ label: String, text: Binding<String>, chooser: PathTarget? = nil, status: String? = nil) -> some View {
        HStack(spacing: 14) {
            Text(label)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
                .frame(width: 170, alignment: .leading)
            styledField(text)
            if let status {
                statusBadge(status)
            }
            if let chooser {
                chooserButton(chooser)
            }
        }
        .frame(minHeight: 42)
    }

    private func sourceRow(title: String, detail: String, isOn: Binding<Bool>, text: Binding<String>, chooser: PathTarget, status: String) -> some View {
        HStack(spacing: 14) {
            Toggle("", isOn: isOn)
                .toggleStyle(.switch)
                .tint(Theme.blue)
                .labelsHidden()
                .frame(width: 56)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 13, weight: .bold))
                Text(detail).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.secondaryText)
            }
            .frame(width: 190, alignment: .leading)
            styledField(text)
            statusBadge(status)
            chooserButton(chooser)
        }
        .frame(minHeight: 50)
    }

    private func toggleRow(_ title: String, detail: String, isOn: Binding<Bool>) -> some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 13, weight: .bold))
                Text(detail).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.secondaryText)
            }
            Spacer()
            Toggle("", isOn: isOn)
                .toggleStyle(.switch)
                .tint(Theme.blue)
                .labelsHidden()
        }
        .frame(minHeight: 42)
    }

    private func compactField(_ label: String, text: Binding<String>, width: CGFloat? = nil) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(label)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
            styledField(text)
        }
        .frame(width: width)
    }

    private func secureField(_ label: String, text: Binding<String>, placeholder: String) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(label)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
            SecureField(placeholder, text: text)
                .textFieldStyle(.plain)
                .font(.system(size: 13, weight: .medium))
                .padding(.horizontal, 10)
                .frame(height: 34)
                .background(fieldBackground)
        }
        .frame(maxWidth: .infinity)
    }

    private func miniToggle(_ title: String, isOn: Binding<Bool>) -> some View {
        Toggle(title, isOn: isOn)
            .toggleStyle(.switch)
            .tint(Theme.blue)
            .font(.system(size: 13, weight: .semibold))
    }

    private func infoRow(_ label: String, _ value: String) -> some View {
        HStack(spacing: 14) {
            Text(label)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
                .frame(width: 170, alignment: .leading)
            Text(value)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(Theme.secondaryText)
                .lineLimit(1)
                .truncationMode(.middle)
            Spacer()
        }
        .frame(minHeight: 34)
    }

    private func styledField(_ text: Binding<String>) -> some View {
        TextField("", text: text)
            .textFieldStyle(.plain)
            .font(.system(size: 13, weight: .medium))
            .padding(.horizontal, 10)
            .frame(height: 34)
            .background(fieldBackground)
    }

    private var fieldBackground: some View {
        RoundedRectangle(cornerRadius: 6)
            .fill(Theme.field)
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.border))
    }

    private func statusBadge(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 12, weight: .bold))
            .foregroundStyle(text == "已找到" || text == "已配置" ? Theme.green : Theme.orange)
            .padding(.horizontal, 11)
            .frame(height: 26)
            .background(RoundedRectangle(cornerRadius: 13).fill(Theme.badge))
    }

    private func chooserButton(_ target: PathTarget) -> some View {
        Button("选择...") {
            choose(target)
        }
        .buttonStyle(.bordered)
        .frame(width: 88)
    }

    private var configStatusText: String {
        FileManager.default.fileExists(atPath: AppPaths.configPath.path) ? "配置文件已创建" : "配置文件未创建"
    }

    private func save() {
        do {
            try FileManager.default.createDirectory(at: AppPaths.configPath.deletingLastPathComponent(), withIntermediateDirectories: true)
            if !apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                try saveSecret(apiKey)
                draft.apiKeyEnv = "DEEPSEEK_API_KEY"
                apiKey = ""
            }
            try draft.toml().write(to: AppPaths.configPath, atomically: true, encoding: .utf8)
            createRuntimeDirectories(for: draft)
            statusMessage = "已保存配置"
        } catch {
            statusMessage = "保存失败：\(error.localizedDescription)"
        }
    }

    private func saveApiKey() {
        do {
            let value = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !value.isEmpty else {
                statusMessage = "请输入 DeepSeek API Key"
                return
            }
            try FileManager.default.createDirectory(at: AppPaths.configPath.deletingLastPathComponent(), withIntermediateDirectories: true)
            try saveSecret(value)
            draft.apiKeyEnv = "DEEPSEEK_API_KEY"
            try draft.toml().write(to: AppPaths.configPath, atomically: true, encoding: .utf8)
            apiKey = ""
            statusMessage = "DeepSeek 密钥已保存"
        } catch {
            statusMessage = "密钥保存失败：\(error.localizedDescription)"
        }
    }

    private func choose(_ target: PathTarget) {
        let panel = NSOpenPanel()
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.canChooseFiles = target.canChooseFiles
        panel.canChooseDirectories = !target.canChooseFiles
        if panel.runModal() == .OK, let path = panel.url?.path {
            switch target {
            case .vault: draft.vaultPath = path
            case .codexSessions: draft.codexSessionsRoot = path
            case .claudeSettings: draft.claudeSettingsPath = path
            case .opencodeStorage: draft.opencodeStorageRoot = path
            case .kunStorage: draft.kunStorageRoot = path
            case .zcodeStorage: draft.zcodeStorageRoot = path
            case .database: draft.databasePath = path
            case .output: draft.outputDir = path
            case .opencodePlugin: draft.opencodePluginPath = path
            case .kunProjectRoot: draft.kunProjectRoot = path
            }
            statusMessage = "有未保存修改"
        }
    }

    private func openDirectory(_ url: URL) {
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        NSWorkspace.shared.open(url)
    }

    private func touchAndOpen(_ path: String) {
        FileManager.default.createFile(atPath: path, contents: nil)
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }
}

private struct SettingsCard<Content: View>: View {
    let title: String
    let subtitle: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.system(size: 17, weight: .bold))
                Text(subtitle)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Theme.secondaryText)
            }
            content
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Theme.card)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border))
        )
    }
}

private enum PathTarget {
    case vault
    case codexSessions
    case claudeSettings
    case opencodeStorage
    case kunStorage
    case zcodeStorage
    case database
    case output
    case opencodePlugin
    case kunProjectRoot

    var canChooseFiles: Bool {
        switch self {
        case .claudeSettings, .database, .opencodePlugin:
            return true
        case .vault, .codexSessions, .opencodeStorage, .kunStorage, .zcodeStorage, .output, .kunProjectRoot:
            return false
        }
    }
}

private enum Theme {
    static let surface = SwiftUI.Color(nsColor: NSColor.hex(0x191D22))
    static let sidebar = SwiftUI.Color(nsColor: NSColor.hex(0x151A20))
    static let footer = SwiftUI.Color(nsColor: NSColor.hex(0x181C22))
    static let card = SwiftUI.Color(nsColor: NSColor.hex(0x20252C))
    static let field = SwiftUI.Color(nsColor: NSColor.hex(0x1A2026))
    static let border = SwiftUI.Color(nsColor: NSColor.hex(0x343B44))
    static let selected = SwiftUI.Color(nsColor: NSColor.hex(0x315AA6))
    static let badge = SwiftUI.Color(nsColor: NSColor.hex(0x2E3943))
    static let primaryText = SwiftUI.Color(nsColor: NSColor.hex(0xF4F6F8))
    static let secondaryText = SwiftUI.Color(nsColor: NSColor.hex(0xAAB4C0))
    static let mutedText = SwiftUI.Color(nsColor: NSColor.hex(0x7D8793))
    static let blue = SwiftUI.Color(nsColor: NSColor.hex(0x3D7DFF))
    static let green = SwiftUI.Color(nsColor: NSColor.hex(0x5FC57B))
    static let orange = SwiftUI.Color(nsColor: NSColor.hex(0xF2B866))
    static let red = SwiftUI.Color(nsColor: NSColor.hex(0xFF6B6B))
}

private func runTerminalCommand(_ command: String) {
    let escaped = command.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
    let script = """
    tell application "Terminal"
      activate
      do script "\(escaped)"
    end tell
    """
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
    process.arguments = ["-e", script]
    try? process.run()
}

private func pathStatus(_ text: String, directory: Bool) -> String {
    let expanded = expandTilde(text)
    let exists = FileManager.default.fileExists(atPath: expanded)
    return exists ? "已找到" : "未找到"
}

private enum Color {
    static let surface = NSColor.hex(0x20242A)
    static let sidebar = NSColor.hex(0x1A1E24)
    static let group = NSColor.hex(0x252A31)
    static let input = NSColor.hex(0x1D2127)
    static let border = NSColor.hex(0x343B44)
    static let primaryText = NSColor.hex(0xF3F5F7)
    static let secondaryText = NSColor.hex(0xAEB6C2)
    static let mutedText = NSColor.hex(0x7D8793)
    static let badge = NSColor.hex(0x303741)
    static let blue = NSColor.hex(0x5BA7FF)
    static let green = NSColor.hex(0x63D68A)
    static let orange = NSColor.hex(0xF2B866)
    static let red = NSColor.hex(0xFF6B6B)
    static let mutedChip = NSColor.hex(0x7D8793)
}

private extension NSColor {
    static func hex(_ hex: UInt32) -> NSColor {
        let red = CGFloat((hex >> 16) & 0xFF) / 255.0
        let green = CGFloat((hex >> 8) & 0xFF) / 255.0
        let blue = CGFloat(hex & 0xFF) / 255.0
        return NSColor(red: red, green: green, blue: blue, alpha: 1.0)
    }
}

private extension Bool {
    var toml: String { self ? "true" : "false" }
}

private extension Dictionary where Key == String, Value == [String: String] {
    func value(_ section: String, _ key: String, _ fallback: String) -> String {
        self[section]?[key] ?? fallback
    }

    func bool(_ section: String, _ key: String, _ fallback: Bool) -> Bool {
        guard let raw = self[section]?[key]?.lowercased() else { return fallback }
        return ["true", "1", "yes", "y"].contains(raw)
    }
}

private func parseTomlSections(_ text: String) -> [String: [String: String]] {
    var sections: [String: [String: String]] = [:]
    var current = ""
    for rawLine in text.components(separatedBy: .newlines) {
        let line = rawLine.trimmingCharacters(in: .whitespaces)
        if line.isEmpty || line.hasPrefix("#") { continue }
        if line.hasPrefix("[") && line.hasSuffix("]") {
            current = String(line.dropFirst().dropLast())
            sections[current, default: [:]] = sections[current] ?? [:]
            continue
        }
        guard let equals = line.firstIndex(of: "=") else { continue }
        let key = String(line[..<equals]).trimmingCharacters(in: .whitespaces)
        let valueText = String(line[line.index(after: equals)...]).trimmingCharacters(in: .whitespaces)
        sections[current, default: [:]][key] = stripTomlValue(valueText)
    }
    return sections
}

private func stripTomlValue(_ value: String) -> String {
    var text = value
    if let hash = text.firstIndex(of: "#") {
        text = String(text[..<hash]).trimmingCharacters(in: .whitespaces)
    }
    if text.hasPrefix("\"") && text.hasSuffix("\"") && text.count >= 2 {
        text = String(text.dropFirst().dropLast())
    }
    return text.replacingOccurrences(of: "\\\"", with: "\"").replacingOccurrences(of: "\\\\", with: "\\")
}

private func tomlString(_ value: String) -> String {
    value.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
}

private func intText(_ value: String, fallback: String) -> String {
    Int(value.trimmingCharacters(in: .whitespacesAndNewlines)) == nil ? fallback : value
}

private func doubleText(_ value: String, fallback: String) -> String {
    Double(value.trimmingCharacters(in: .whitespacesAndNewlines)) == nil ? fallback : value
}

private func defaultOpenCodeEnabled() -> Bool {
    let storage = AppPaths.home.appendingPathComponent(".local/share/opencode/storage").path
    let pluginDir = AppPaths.home.appendingPathComponent(".config/opencode/plugins").path
    return FileManager.default.fileExists(atPath: storage) || FileManager.default.fileExists(atPath: pluginDir)
}

private func defaultKunEnabled() -> Bool {
    FileManager.default.fileExists(atPath: AppPaths.home.appendingPathComponent(".kun/data").path)
        || FileManager.default.fileExists(atPath: AppPaths.home.appendingPathComponent(".deepseekgui/kun").path)
}

private func defaultZCodeEnabled() -> Bool {
    FileManager.default.fileExists(atPath: AppPaths.home.appendingPathComponent(".zcode/cli").path)
}

private func expandTilde(_ value: String) -> String {
    NSString(string: value).expandingTildeInPath
}

private func createRuntimeDirectories(for config: WorkJournalConfig) {
    let databaseParent = URL(fileURLWithPath: expandTilde(config.databasePath)).deletingLastPathComponent()
    try? FileManager.default.createDirectory(at: databaseParent, withIntermediateDirectories: true)
    try? FileManager.default.createDirectory(atPath: expandTilde(config.outputDir), withIntermediateDirectories: true)
    let vault = config.vaultPath.trimmingCharacters(in: .whitespacesAndNewlines)
    if !vault.isEmpty {
        let vaultPath = expandTilde(vault)
        try? FileManager.default.createDirectory(atPath: "\(vaultPath)/\(config.dailyDir)", withIntermediateDirectories: true)
        try? FileManager.default.createDirectory(atPath: "\(vaultPath)/\(config.taskDir)", withIntermediateDirectories: true)
        if config.writeKnowledgeNotes {
            try? FileManager.default.createDirectory(atPath: "\(vaultPath)/\(config.knowledgeDir)", withIntermediateDirectories: true)
        }
    }
}

private func saveSecret(_ apiKey: String) throws {
    let escaped = apiKey.replacingOccurrences(of: "'", with: "'\\''")
    let content = "export DEEPSEEK_API_KEY='\(escaped)'\n"
    try FileManager.default.createDirectory(at: AppPaths.secretsPath.deletingLastPathComponent(), withIntermediateDirectories: true)
    try content.write(to: AppPaths.secretsPath, atomically: true, encoding: .utf8)
    chmod(AppPaths.secretsPath.path, S_IRUSR | S_IWUSR)
}

private func shellQuote(_ value: String) -> String {
    "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
}

private let app = NSApplication.shared
private let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
