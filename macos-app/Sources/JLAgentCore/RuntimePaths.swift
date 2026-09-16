import Foundation

public struct RuntimePaths: Equatable, Sendable {
  public let root: URL
  public let socket: URL
  public let consentSocket: URL
  public let credential: URL
  public let readiness: URL
  public let consentPublicKey: URL

  public var hermes: URL {
    root.deletingLastPathComponent().appendingPathComponent("hermes")
  }

  public var modelCache: URL {
    root.deletingLastPathComponent().appendingPathComponent("cache/models")
  }

  public var logs: URL {
    root.deletingLastPathComponent().appendingPathComponent("logs")
  }

  public init(root: URL? = nil) {
    let base =
      root
      ?? FileManager.default.homeDirectoryForCurrentUser
      .appendingPathComponent("Library/Application Support/JL Agent/runtime")
    self.root = base
    self.socket = base.appendingPathComponent("jl-agent.sock")
    self.consentSocket = base.appendingPathComponent("jl-agent-consent.sock")
    self.credential = base.appendingPathComponent("ipc.credential")
    self.readiness = base.appendingPathComponent("ready.json")
    self.consentPublicKey =
      base
      .appendingPathComponent("native-consent-public-key.der")
  }
}
