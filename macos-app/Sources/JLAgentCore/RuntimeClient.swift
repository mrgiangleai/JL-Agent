import Foundation

public enum RuntimeClientError: Error, Equatable, LocalizedError, Sendable {
  case missingCredential
  case credential(String)
  case invalidRequest(String)
  case transport(String)
  case timeout
  case requestTooLarge
  case responseTooLarge
  case malformedResponse
  case unsupportedProtocol(Int)
  case mismatchedResponse
  case server(code: String, message: String)

  public var errorDescription: String? {
    switch self {
    case .missingCredential:
      "IPC credential is missing. Start the runtime, then refresh the credential."
    case .credential(let message), .invalidRequest(let message),
      .transport(let message):
      message
    case .timeout: "Runtime request timed out."
    case .requestTooLarge: "Runtime request exceeds the 64 KiB limit."
    case .responseTooLarge: "Runtime response exceeds the 64 KiB limit."
    case .malformedResponse: "Runtime returned a malformed response."
    case .unsupportedProtocol(let version):
      "Runtime protocol version \(version) is unsupported."
    case .mismatchedResponse: "Runtime response identity does not match the request."
    case .server(let code, let message): "\(code): \(message)"
    }
  }
}

public enum RuntimeStatusSnapshotPolicy {
  public static func shouldRetain(after error: Error) -> Bool {
    guard let runtimeError = error as? RuntimeClientError else { return false }
    if case .server(let code, _) = runtimeError {
      return code != "authentication_failed"
    }
    return false
  }
}

public enum ConsentExpiryPolicy {
  public static func deadline(
    receivedAt: ContinuousClock.Instant,
    expiresInSeconds: Int
  ) -> ContinuousClock.Instant {
    receivedAt.advanced(by: .seconds(max(0, expiresInSeconds)))
  }

  public static func isExpired(
    deadline: ContinuousClock.Instant,
    now: ContinuousClock.Instant
  ) -> Bool {
    now >= deadline
  }
}

public struct PrepareResult: Equatable, Sendable {
  public let state: String
  public let challenge: ConsentChallenge?
}

public struct ExecutionDisplayResult: Equatable, Sendable {
  public let state: String
  public let output: String?
}

public struct JLRuntimeClient: Sendable {
  public let paths: RuntimePaths
  private let credentials: any CredentialProviding
  private let transport: any IPCTransport
  private let timeout: TimeInterval
  private let maximumBytes: Int

  public init(
    paths: RuntimePaths = RuntimePaths(),
    credentials: any CredentialProviding = KeychainCredentialProvider(),
    transport: any IPCTransport = UnixSocketTransport(),
    timeout: TimeInterval = defaultTimeoutSeconds,
    maximumBytes: Int = maximumMessageBytes
  ) {
    self.paths = paths
    self.credentials = credentials
    self.transport = transport
    self.timeout = timeout
    self.maximumBytes = maximumBytes
  }

  public func status(callerID: String, sessionID: String) throws -> RuntimeStatus {
    let result = try authenticatedRequest(
      operation: "status",
      payload: [:],
      callerID: callerID,
      sessionID: sessionID
    )
    return try RuntimeStatus(result: result)
  }

  public func activity(
    callerID: String,
    sessionID: String,
    limit: Int = 50
  ) throws -> [ActivityEvent] {
    let result = try authenticatedRequest(
      operation: "activity",
      payload: ["limit": .integer(limit)],
      callerID: callerID,
      sessionID: sessionID
    )
    guard let values = result["events"]?.arrayValue else {
      throw RuntimeClientError.malformedResponse
    }
    return try values.map(ActivityEvent.init(value:))
  }

  public func prepare(
    draft: RequestDraft,
    requestID: String,
    callerID: String,
    sessionID: String
  ) throws -> PrepareResult {
    let result = try authenticatedRequest(
      operation: "prepare",
      payload: draft.payload(),
      requestID: requestID,
      callerID: callerID,
      sessionID: sessionID
    )
    guard let state = result["state"]?.stringValue else {
      throw RuntimeClientError.malformedResponse
    }
    switch state {
    case "prepared":
      return PrepareResult(state: state, challenge: nil)
    case "awaiting_approval":
      return PrepareResult(
        state: state,
        challenge: try ConsentChallenge(result: result)
      )
    default:
      throw RuntimeClientError.malformedResponse
    }
  }

  public func execute(
    draft: RequestDraft,
    requestID: String,
    callerID: String,
    sessionID: String
  ) throws -> ExecutionDisplayResult {
    let result = try authenticatedRequest(
      operation: "execute",
      payload: draft.payload(),
      requestID: requestID,
      callerID: callerID,
      sessionID: sessionID
    )
    guard let state = result["state"]?.stringValue else {
      throw RuntimeClientError.malformedResponse
    }
    return ExecutionDisplayResult(
      state: state,
      output: result["output"]?.stringValue
    )
  }

  public func submitConsent(
    challenge: ConsentChallenge,
    decision: ConsentDecision,
    signature: String
  ) throws -> String {
    let result = try request(
      endpoint: paths.consentSocket,
      envelope: RequestEnvelope(
        protocolVersion: protocolVersion,
        requestID: challenge.requestID,
        callerID: challenge.callerID,
        sessionID: challenge.sessionID,
        operation: "consent-decision",
        payload: [
          "consent_id": .string(challenge.consentID),
          "decision": .string(decision.rawValue),
          "signature": .string(signature),
        ],
        credential: nil
      )
    )
    guard let state = result["state"]?.stringValue else {
      throw RuntimeClientError.malformedResponse
    }
    return state
  }

  private func authenticatedRequest(
    operation: String,
    payload: [String: JSONValue],
    requestID: String = UUID().uuidString.lowercased(),
    callerID: String,
    sessionID: String
  ) throws -> [String: JSONValue] {
    let credential = try credentials.loadCredential()
    return try request(
      endpoint: paths.socket,
      envelope: RequestEnvelope(
        protocolVersion: protocolVersion,
        requestID: requestID,
        callerID: callerID,
        sessionID: sessionID,
        operation: operation,
        payload: payload,
        credential: credential
      )
    )
  }

  private func request(
    endpoint: URL,
    envelope: RequestEnvelope
  ) throws -> [String: JSONValue] {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    var encoded = try encoder.encode(envelope)
    encoded.append(0x0A)
    guard encoded.count <= maximumBytes else {
      throw RuntimeClientError.requestTooLarge
    }
    let responseData = try transport.send(
      endpoint: endpoint,
      data: encoded,
      timeout: timeout,
      maximumResponseBytes: maximumBytes
    )
    guard responseData.count <= maximumBytes else {
      throw RuntimeClientError.responseTooLarge
    }
    try validateResponseShape(responseData)
    let response: ResponseEnvelope
    do {
      response = try JSONDecoder().decode(ResponseEnvelope.self, from: responseData)
    } catch {
      throw RuntimeClientError.malformedResponse
    }
    guard response.protocolVersion == protocolVersion else {
      throw RuntimeClientError.unsupportedProtocol(response.protocolVersion)
    }
    guard response.requestID == envelope.requestID else {
      throw RuntimeClientError.mismatchedResponse
    }
    if response.ok {
      guard let result = response.result, response.error == nil else {
        throw RuntimeClientError.malformedResponse
      }
      return result
    }
    guard let error = response.error, response.result == nil else {
      throw RuntimeClientError.malformedResponse
    }
    throw RuntimeClientError.server(code: error.code, message: error.message)
  }

  private func validateResponseShape(_ data: Data) throws {
    guard
      let object = try? JSONSerialization.jsonObject(with: data),
      let mapping = object as? [String: Any],
      Set(mapping.keys) == [
        "protocol_version", "request_id", "ok", "result", "error",
      ]
    else {
      throw RuntimeClientError.malformedResponse
    }
  }
}
