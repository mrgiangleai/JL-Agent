import Darwin
import Foundation

@main
struct JLVoiceRuntimeHost {
  static func main() {
    guard let resourceURL = Bundle.main.resourceURL else {
      fail("JL Voice Runtime has no resource directory.")
    }
    let sharedLauncher = Bundle.main.bundleURL.deletingLastPathComponent()
      .appendingPathComponent("JLRuntime/run-runtime.sh")
    let nestedLauncher = resourceURL.appendingPathComponent("JLRuntime/run-runtime.sh")
    let launcher = FileManager.default.isExecutableFile(atPath: sharedLauncher.path)
      ? sharedLauncher
      : nestedLauncher
    guard FileManager.default.isExecutableFile(atPath: launcher.path) else {
      fail("JL Voice Runtime is missing its packaged JL runtime launcher.")
    }

    var environment = ProcessInfo.processInfo.environment
    environment["JL_AGENT_VOICE_ENABLED"] = "1"
    environment["JL_AGENT_VOICE_ACTIVATION_APPROVED"] = "1"
    environment["HERMES_DISABLE_LAZY_INSTALLS"] = "1"

    let runtime = Process()
    runtime.executableURL = URL(fileURLWithPath: "/bin/zsh")
    runtime.arguments = [launcher.path] + Array(CommandLine.arguments.dropFirst())
    runtime.currentDirectoryURL = launcher.deletingLastPathComponent()
    runtime.environment = environment
    runtime.standardInput = FileHandle.standardInput
    runtime.standardOutput = FileHandle.standardOutput
    runtime.standardError = FileHandle.standardError

    signal(SIGTERM, SIG_IGN)
    signal(SIGINT, SIG_IGN)
    let signalSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .global())
    signalSource.setEventHandler {
      if runtime.isRunning { runtime.terminate() }
    }
    signalSource.resume()

    do {
      try runtime.run()
      runtime.waitUntilExit()
    } catch {
      signalSource.cancel()
      fail("JL Voice Runtime could not start JL runtime: \(error.localizedDescription)")
    }
    signalSource.cancel()
    exit(runtime.terminationStatus)
  }

  private static func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(69)
  }
}
