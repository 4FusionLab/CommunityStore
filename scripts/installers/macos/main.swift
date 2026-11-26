import Cocoa

final class Installer: NSObject, NSApplicationDelegate {
    private let russian = Locale.preferredLanguages.first?.hasPrefix("ru") == true
    private var window: NSWindow!
    private var heading: NSTextField!
    private var message: NSTextField!
    private var emblem: NSImageView!
    private var button: NSButton!
    private var dismiss: NSButton!
    private var progress: NSProgressIndicator!
    private var stages = [NSTextField]()
    private var installing = false
    private var finished = false
    private var fusionLauncher: URL?

    private func text(_ english: String, _ russianText: String) -> String {
        russian ? russianText : english
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 520, height: 314),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Community Store"
        window.isReleasedWhenClosed = false
        emblem = NSImageView(frame: NSRect(x: 31, y: 228, width: 48, height: 48))
        if let icon = Bundle.main.url(forResource: "StoreMark", withExtension: "png") {
            emblem.image = NSImage(contentsOf: icon)
        }
        emblem.imageScaling = .scaleProportionallyUpOrDown
        heading = NSTextField(labelWithString: text("Community Store for Fusion", "Community Store для Fusion"))
        heading.font = .systemFont(ofSize: 23, weight: .semibold)
        heading.frame = NSRect(x: 97, y: 233, width: 392, height: 36)

        message = NSTextField(wrappingLabelWithString: text(
            "Explore and install add-ins inside Autodesk Fusion. Save your work and close Fusion before you start.",
            "Находите и устанавливайте расширения прямо в Autodesk Fusion. Перед началом сохраните работу и закройте Fusion."))
        message.font = .systemFont(ofSize: 14)
        message.frame = NSRect(x: 32, y: 144, width: 456, height: 66)

        let labels = [text("Download", "Скачивание"), text("Verify", "Проверка"), text("Install", "Установка")]
        for (index, label) in labels.enumerated() {
            let stage = NSTextField(labelWithString: label)
            stage.font = .systemFont(ofSize: 13, weight: .medium)
            stage.frame = NSRect(x: 32 + index * 152, y: 103, width: 148, height: 19)
            stages.append(stage)
        }
        progress = NSProgressIndicator(frame: NSRect(x: 32, y: 80, width: 456, height: 12))
        progress.style = .bar
        progress.isIndeterminate = false
        progress.maxValue = 3
        progress.controlSize = .small
        progress.isDisplayedWhenStopped = true

        button = NSButton(title: text("Install", "Установить"), target: self, action: #selector(primary))
        button.bezelStyle = .rounded
        button.keyEquivalent = "\r"
        button.frame = NSRect(x: 336, y: 23, width: 153, height: 33)
        dismiss = NSButton(title: text("Done", "Готово"), target: self, action: #selector(close))
        dismiss.bezelStyle = .rounded
        dismiss.frame = NSRect(x: 205, y: 23, width: 123, height: 33)
        dismiss.isHidden = true
        for view in [heading, emblem, message, progress, dismiss, button] as [NSView] {
            window.contentView?.addSubview(view)
        }
        for stage in stages { window.contentView?.addSubview(stage) }
        setStage(0)
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func primary() {
        if finished {
            if let url = fusionLauncher {
                NSWorkspace.shared.openApplication(at: url, configuration: .init()) { _, error in
                    DispatchQueue.main.async {
                        if error == nil {
                            NSApp.terminate(nil)
                        } else {
                            self.fusionLauncher = nil
                            self.button.title = self.text("Done", "Готово")
                            self.dismiss.isHidden = true
                        }
                    }
                }
            } else {
                close()
            }
            return
        }
        installing = true
        progress.isHidden = false
        for stage in stages { stage.isHidden = false }
        button.isEnabled = false
        window.standardWindowButton(.closeButton)?.isEnabled = false
        message.stringValue = text("Looking up the latest stable release…", "Ищем последний стабильный релиз…")
        setStage(0)
        guard let helper = Bundle.main.url(forAuxiliaryExecutable: "community-store-downloader") else {
            complete(30)
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            let pipe = Pipe()
            process.executableURL = helper
            process.arguments = ["--progress"]
            process.standardOutput = pipe
            do {
                try process.run()
            } catch {
                DispatchQueue.main.async { self.complete(30) }
                return
            }
            var pending = Data()
            let output = pipe.fileHandleForReading
            while true {
                let chunk = output.availableData
                if chunk.isEmpty { break }
                pending.append(chunk)
                while let newline = pending.firstIndex(of: 10) {
                    let line = String(decoding: pending[..<newline], as: UTF8.self)
                    pending.removeSubrange(...newline)
                    DispatchQueue.main.async { self.update(line) }
                }
            }
            process.waitUntilExit()
            let code: Int32 = process.terminationReason == .exit ? process.terminationStatus : 30
            DispatchQueue.main.async { self.complete(code) }
        }
    }

    private func update(_ line: String) {
        switch line {
        case "download":
            message.stringValue = text("Downloading Community Store…", "Скачиваем Community Store…")
        case "verify":
            setStage(1)
            message.stringValue = text("Checking the archive and release checksum…", "Проверяем архив и контрольную сумму…")
        case "install":
            setStage(2)
            message.stringValue = text("Adding Community Store to Fusion…", "Добавляем Community Store в Fusion…")
        default: break
        }
    }

    private func setStage(_ count: Int) {
        progress.doubleValue = Double(count)
        for (index, stage) in stages.enumerated() {
            stage.textColor = index <= count ? .labelColor : .secondaryLabelColor
        }
    }

    private func complete(_ code: Int32) {
        installing = false
        button.isEnabled = true
        window.standardWindowButton(.closeButton)?.isEnabled = true
        switch code {
        case 0:
            finished = true
            setStage(3)
            heading.stringValue = text("Ready in Fusion", "Всё готово")
            emblem.image = NSImage(systemSymbolName: "checkmark.circle.fill", accessibilityDescription: text("Installed", "Установлено"))
            emblem.contentTintColor = .systemGreen
            let workspace = NSWorkspace.shared
            if !workspace.accessibilityDisplayShouldReduceMotion {
                emblem.alphaValue = 0.35
                NSAnimationContext.runAnimationGroup { context in
                    context.duration = 0.22
                    emblem.animator().alphaValue = 1
                }
            }
            let running = workspace.runningApplications.contains { $0.bundleIdentifier == "com.autodesk.fusion360" }
            if !running {
                fusionLauncher = workspace.urlForApplication(withBundleIdentifier: "com.autodesk.dls.streamer.scriptapp.Autodesk-Fusion")
            }
            let path = "Design → Utilities → Add-Ins → Community Store"
            message.stringValue = running
                ? text("Restart Fusion and choose:\n\(path)", "Перезапустите Fusion и выберите:\n\(path)")
                : text("Open Fusion and choose:\n\(path)", "Откройте Fusion и выберите:\n\(path)")
            if let image = InstallerImage.containing(Bundle.main.bundleURL), (try? image.schedule()) != nil {
                message.stringValue += text("\nThe disk image will move to Trash when you close Setup.",
                                            "\nОбраз диска отправится в Корзину при закрытии.")
            }
        case 10:
            finished = true
            heading.stringValue = text("Community Store is already installed", "Community Store уже установлен")
            message.stringValue = text("Open it from Utilities → Add-Ins in Fusion. Use Community Store settings to check for updates.",
                                       "Откройте его через Utilities → Add-Ins в Fusion. Обновления доступны в настройках Community Store.")
        case 21:
            heading.stringValue = text("No stable release yet", "Стабильного релиза пока нет")
            message.stringValue = text("Try again after 4FusionLab publishes Community Store on GitHub.",
                                       "Повторите попытку, когда 4FusionLab опубликует Community Store на GitHub.")
        case 20:
            heading.stringValue = text("The download couldn’t be verified", "Релиз не удалось проверить")
            message.stringValue = text("Check your internet connection and try again.",
                                       "Проверьте подключение к интернету и повторите попытку.")
        default:
            heading.stringValue = text("Installation didn’t finish", "Установка не завершилась")
            message.stringValue = text("Check that you can write to the Autodesk folder in your user account and try again.",
                                       "Проверьте доступ на запись в папку Autodesk в своём профиле и повторите попытку.")
        }
        if fusionLauncher != nil {
            button.title = text("Open Fusion", "Открыть Fusion")
            dismiss.isHidden = false
        } else {
            button.title = finished ? text("Done", "Готово") : text("Retry", "Повторить")
        }
        if code != 0 {
            progress.isHidden = true
            for stage in stages { stage.isHidden = true }
        }
        NSAccessibility.post(element: heading!, notification: .valueChanged)
    }

    @objc private func close() {
        NSApp.terminate(nil)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        installing ? .terminateCancel : .terminateNow
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

if CommandLine.arguments.count == 7 && CommandLine.arguments[1] == "--recycle-image" {
    InstallerImage.recycle(CommandLine.arguments)
} else {
    let app = NSApplication.shared
    let installer = Installer()
    app.delegate = installer
    app.setActivationPolicy(.regular)
    app.run()
}
