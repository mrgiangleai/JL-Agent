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
/// An already-ready runtime is treated as the owner for the current session;
/// the runtime's authenticated socket and stale-endpoint checks remain the
/// final duplicate-owner authority.
public final class RuntimeProcessController: @unchecked Sendable {
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
    if isReady() { return }
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
    if isReady() { return }
    if let process = currentProcess(), process.isRunning { return }
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
    guard let process = currentProcess(), process.isRunning else { return false }
    let runtimePID = readyRuntimePID()
    process.terminate()
    let deadline = Date().addingTimeInterval(3)
    while process.isRunning && Date() < deadline {
      Thread.sleep(forTimeInterval: 0.05)
    }
    if process.isRunning {
      _ = kill(process.processIdentifier, SIGKILL)
    }
    if let runtimePID,
      runtimePID != process.processIdentifier,
      isProcessAlive(runtimePID)
    {
      _ = kill(runtimePID, SIGTERM)
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
      let pid = payload["pid"] as? Int,
      pid > 0,
      FileManager.default.fileExists(atPath: paths.socket.path)
    else { return false }
    return kill(pid_t(pid), 0) == 0 || errno == EPERM
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
