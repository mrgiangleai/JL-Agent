import Darwin
import Foundation

public enum RuntimeProcessError: Error, LocalizedError, Sendable {
  case launcherUnavailable(URL)
  case startFailed(String)
  case readinessTimeout

  public var errorDescription: String? {
    switch self {
    case .launcherUnavailable(let url):
      "Packaged JL runtime launcher is missing: \(url.path)"
    case .startFailed(let message):
      "JL runtime could not start: \(message)"
    case .readinessTimeout:
      "JL runtime did not become ready before the startup deadline."
    }
  }
}

/// Starts only the packaged runtime owned by this app instance.
///
/// A ready runtime is reused only while this controller owns its launcher.
/// A marker left by a crashed or force-terminated app is reclaimed before a
/// new launcher is started, preventing an orphaned runtime from becoming a
/// hidden duplicate owner.
public final class RuntimeProcessController: @unchecked Sendable {
  private static let runtimeCompatibility = 2

  public let paths: RuntimePaths

  private let launcherURL: URL
  private let lock = NSLock()
  private var process: Process?
  private var logHandle: FileHandle?

  public init(
    paths: RuntimePaths = RuntimePaths(),
    launcherURL: URL? = nil,
    bundle: Bundle = .main
  ) {
    self.paths = paths
    self.launcherURL =
      launcherURL
      ?? bundle.resourceURL?.appendingPathComponent(
        "JLVoiceRuntime.app/Contents/MacOS/JLVoiceRuntime"
      )
      ?? bundle.resourceURL?.appendingPathComponent("JLRuntime/run-runtime.sh")
      ?? URL(fileURLWithPath: "/__missing__/JLRuntime/run-runtime.sh")
  }

  deinit {
    _ = stopOwnedRuntime()
  }

  public func ensureReady(timeout: TimeInterval = 15) async throws {
    if currentProcess() != nil && isReady() { return }
    try startIfNeeded()
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
      if isReady() { return }
      if let process = currentProcess(), !process.isRunning {
        throw RuntimeProcessError.startFailed(
          "launcher exited with status \(process.terminationStatus)"
        )
      }
      try await Task.sleep(for: .milliseconds(100))
    }
    throw RuntimeProcessError.readinessTimeout
  }

  public func startIfNeeded() throws {
    if let process = currentProcess(), process.isRunning {
      if hasStaleReadinessMarker() {
        _ = stopOwnedRuntime()
      } else {
        return
      }
    }
    try recoverUnownedRuntimeIfNeeded()
    try recoverStaleRuntimeIfNeeded()
    guard FileManager.default.isExecutableFile(atPath: launcherURL.path) else {
      throw RuntimeProcessError.launcherUnavailable(launcherURL)
    }

    let output = try openDiagnosticLog()
    let child = Process()
    if launcherURL.pathExtension == "sh" {
      child.executableURL = URL(fileURLWithPath: "/bin/zsh")
      child.arguments = [launcherURL.path]
    } else {
      child.executableURL = launcherURL
      child.arguments = []
    }
    child.currentDirectoryURL = launcherURL.deletingLastPathComponent()
    child.standardOutput = output
    child.standardError = output
    child.terminationHandler = { [weak self] _ in
      self?.lock.lock()
      self?.process = nil
      self?.logHandle = nil
      self?.lock.unlock()
    }

    do {
      try child.run()
    } catch {
      output.closeFile()
      throw RuntimeProcessError.startFailed(error.localizedDescription)
    }
    lock.lock()
    process = child
    logHandle = output
    lock.unlock()
  }

  public func restart() async throws {
    stopOwnedRuntime()
    try await ensureReady()
  }

  @discardableResult
  public func stopOwnedRuntime() -> Bool {
    let process = currentProcess()
    let runtimePID = readyRuntimePID()
    let processIsRunning = process?.isRunning == true
    let runtimeIsRunning = runtimePID.map(isProcessAlive) == true
    guard processIsRunning || runtimeIsRunning else { return false }

    if let process, process.isRunning {
      process.terminate()
      let deadline = Date().addingTimeInterval(3)
      while process.isRunning && Date() < deadline {
        Thread.sleep(forTimeInterval: 0.05)
      }
      if process.isRunning {
        _ = kill(process.processIdentifier, SIGKILL)
      }
    }

    if let runtimePID,
      isProcessAlive(runtimePID)
    {
      _ = kill(runtimePID, SIGTERM)
      let deadline = Date().addingTimeInterval(3)
      while isProcessAlive(runtimePID) && Date() < deadline {
        Thread.sleep(forTimeInterval: 0.05)
      }
      if isProcessAlive(runtimePID) {
        _ = kill(runtimePID, SIGKILL)
      }
    }
    lock.lock()
    self.process = nil
    self.logHandle = nil
    lock.unlock()
    return true
  }

  private func currentProcess() -> Process? {
    lock.lock()
    defer { lock.unlock() }
    return process
  }

  private func readyRuntimePID() -> pid_t? {
    guard
      let data = try? Data(contentsOf: paths.readiness, options: [.uncached]),
      let object = try? JSONSerialization.jsonObject(with: data),
      let payload = object as? [String: Any],
      let value = payload["pid"] as? Int,
      value > 0
    else { return nil }
    return pid_t(value)
  }

  private func isProcessAlive(_ pid: pid_t) -> Bool {
    kill(pid, 0) == 0 || errno == EPERM
  }

  private func isReady() -> Bool {
    guard
      let data = try? Data(contentsOf: paths.readiness, options: [.uncached]),
      let object = try? JSONSerialization.jsonObject(with: data),
      let payload = object as? [String: Any],
      payload["ready"] as? Bool == true,
      payload["jl_runtime_compatibility"] as? Int == Self.runtimeCompatibility,
      let pid = payload["pid"] as? Int,
      pid > 0,
      FileManager.default.fileExists(atPath: paths.socket.path)
    else { return false }
    return kill(pid_t(pid), 0) == 0 || errno == EPERM
  }

  private func hasStaleReadinessMarker() -> Bool {
    guard FileManager.default.fileExists(atPath: paths.readiness.path) else { return false }
    guard
      let data = try? Data(contentsOf: paths.readiness, options: [.uncached]),
      let object = try? JSONSerialization.jsonObject(with: data),
      let payload = object as? [String: Any]
    else { return true }
    guard
      payload["ready"] as? Bool == true,
      payload["jl_runtime_compatibility"] as? Int == Self.runtimeCompatibility,
      let pid = payload["pid"] as? Int,
      pid > 0
    else {
      return true
    }
    guard FileManager.default.fileExists(atPath: paths.socket.path) else {
      return true
    }
    return !(kill(pid_t(pid), 0) == 0 || errno == EPERM)
  }

  private func recoverStaleRuntimeIfNeeded() throws {
    guard hasStaleReadinessMarker() else { return }
    guard
      let data = try? Data(contentsOf: paths.readiness, options: [.uncached]),
      let object = try? JSONSerialization.jsonObject(with: data),
      let payload = object as? [String: Any]
    else { return }
    guard
      let details = try? FileManager.default.attributesOfItem(atPath: paths.readiness.path),
      (details[.ownerAccountID] as? NSNumber)?.intValue ?? -1 == Int(getuid()),
      let mode = (details[.posixPermissions] as? NSNumber)?.intValue,
      mode == 0o600
    else { return }
    guard let value = payload["pid"] as? Int, value > 0 else { return }

    let pid = pid_t(value)
    if isProcessAlive(pid) {
      _ = kill(pid, SIGTERM)
      let deadline = Date().addingTimeInterval(2)
      while isProcessAlive(pid) && Date() < deadline {
        Thread.sleep(forTimeInterval: 0.05)
      }
      if isProcessAlive(pid) {
        throw RuntimeProcessError.startFailed("stale JL runtime is still active")
      }
    }
  }

  private func recoverUnownedRuntimeIfNeeded() throws {
    guard currentProcess() == nil else { return }
    guard let runtimePID = readyRuntimePID(), isProcessAlive(runtimePID) else {
      return
    }

    _ = kill(runtimePID, SIGTERM)
    let deadline = Date().addingTimeInterval(3)
    while isProcessAlive(runtimePID) && Date() < deadline {
      Thread.sleep(forTimeInterval: 0.05)
    }
    if isProcessAlive(runtimePID) {
      _ = kill(runtimePID, SIGKILL)
    }
    if isProcessAlive(runtimePID) {
      throw RuntimeProcessError.startFailed("orphaned JL runtime is still active")
    }
  }

  private func openDiagnosticLog() throws -> FileHandle {
    let directory = paths.logs
    try FileManager.default.createDirectory(
      at: directory,
      withIntermediateDirectories: true,
      attributes: [.posixPermissions: 0o700]
    )
    let path = directory.appendingPathComponent("runtime.log").path
    let descriptor = open(path, O_WRONLY | O_CREAT | O_APPEND, 0o600)
    guard descriptor >= 0 else {
      throw RuntimeProcessError.startFailed("cannot open runtime diagnostic log")
    }
    fchmod(descriptor, 0o600)
    return FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
  }
}
