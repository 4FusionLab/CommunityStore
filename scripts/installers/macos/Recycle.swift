import Foundation
import Darwin

struct InstallerImage {
    let file: URL
    let mount: String
    let identity: [String]

    private static func identity(of file: URL) -> [String]? {
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: file.path),
              attributes[.type] as? FileAttributeType == .typeRegular,
              let device = attributes[.systemNumber] as? NSNumber,
              let inode = attributes[.systemFileNumber] as? NSNumber else { return nil }
        return [device.stringValue, inode.stringValue]
    }

    static func mountedImages() -> [(file: URL, mounts: [String])] {
        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/hdiutil")
        process.arguments = ["info", "-plist"]
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        guard (try? process.run()) != nil else { return [] }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0,
              let info = (try? PropertyListSerialization.propertyList(from: data, format: nil)) as? [String: Any],
              let images = info["images"] as? [[String: Any]] else { return [] }
        return images.compactMap { image in
            guard let path = image["image-path"] as? String, path.hasPrefix("/"),
                  let entities = image["system-entities"] as? [[String: Any]] else { return nil }
            let file = URL(fileURLWithPath: path).standardizedFileURL
            guard file.pathExtension.lowercased() == "dmg" else { return nil }
            let mounts = entities.compactMap { entity in
                (entity["mount-point"] as? String).map {
                    URL(fileURLWithPath: $0).resolvingSymlinksInPath().path
                }
            }
            return (file, mounts)
        }
    }

    static func containing(_ app: URL) -> InstallerImage? {
        let mount = app.deletingLastPathComponent().resolvingSymlinksInPath().path
        for image in mountedImages() where image.mounts.contains(mount) {
            if let identity = identity(of: image.file) {
                return InstallerImage(file: image.file, mount: mount, identity: identity)
            }
        }
        return nil
    }

    func schedule() throws {
        guard let executable = Bundle.main.executableURL else { return }
        let manager = FileManager.default
        let stage = manager.temporaryDirectory.appendingPathComponent("community-store-recycle-" + UUID().uuidString)
        try manager.createDirectory(at: stage, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        do {
            let worker = stage.appendingPathComponent("recycle")
            try manager.copyItem(at: executable, to: worker)
            let process = Process()
            process.executableURL = worker
            process.arguments = ["--recycle-image", String(getpid()), file.path, mount] + identity
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            try process.run()
        } catch {
            try? manager.removeItem(at: stage)
            throw error
        }
    }

    static func recycle(_ args: [String]) {
        let worker = URL(fileURLWithPath: args[0]).standardizedFileURL
        let stage = worker.deletingLastPathComponent()
        let manager = FileManager.default
        guard stage.lastPathComponent.hasPrefix("community-store-recycle-"),
              stage.deletingLastPathComponent().resolvingSymlinksInPath().path == manager.temporaryDirectory.resolvingSymlinksInPath().path,
              let pid = Int32(args[2]), pid > 1,
              getppid() == pid || (getppid() == 1 && kill(pid, 0) != 0) else { return }
        defer { try? manager.removeItem(at: stage) }
        let file = URL(fileURLWithPath: args[3]).standardizedFileURL
        let mount = args[4]
        let original = Array(args[5...6])
        guard file.pathExtension.lowercased() == "dmg", identity(of: file) == original,
              mountedImages().contains(where: { $0.file == file && $0.mounts.contains(mount) }) else { return }
        let deadline = Date().addingTimeInterval(30)
        while kill(pid, 0) == 0 && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.15)
        }
        guard kill(pid, 0) != 0, identity(of: file) == original else { return }
        if mountedImages().contains(where: { $0.file == file && $0.mounts.contains(mount) }) {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/hdiutil")
            process.arguments = ["detach", mount]
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            if (try? process.run()) != nil { process.waitUntilExit() }
        }
        guard identity(of: file) == original else { return }
        try? manager.trashItem(at: file, resultingItemURL: nil)
    }
}
