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
  private let executionTimeout: TimeInterval
  private let maximumBytes: Int

  public init(
    paths: RuntimePaths = RuntimePaths(),
    credentials: any CredentialProviding = KeychainCredentialProvider(),
    transport: any IPCTransport = UnixSocketTransport(),
    timeout: TimeInterval = defaultTimeoutSeconds,
    executionTimeout: TimeInterval = executionResponseTimeoutSeconds,
    maximumBytes: Int = maximumMessageBytes
  ) {
    self.paths = paths
    self.credentials = credentials
    self.transport = transport
    self.timeout = timeout
    self.executionTimeout = executionTimeout
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

  public func skillsList(
    callerID: String,
    sessionID: String,
    limit: Int = 100,
    offset: Int = 0
  ) throws -> ManagedSkillPage {
    let result = try authenticatedRequest(
      operation: "skills-list",
      payload: [
        "limit": .integer(min(max(1, limit), 100)),
        "offset": .integer(min(max(0, offset), 1_000_000)),
      ],
      callerID: callerID,
      sessionID: sessionID
    )
    return try ManagedSkillPage(result: result)
  }

  public func skillPreview(
    name: String,
    maxChars: Int = 2_048,
    callerID: String,
    sessionID: String
  ) throws -> SkillPreview {
    let result = try authenticatedRequest(
      operation: "skill-preview",
      payload: [
        "name": .string(name),
        "max_chars": .integer(min(max(1, maxChars), 8_192)),
      ],
      callerID: callerID,
      sessionID: sessionID
    )
    return try SkillPreview(result: result)
  }

  public func importSkill(
    sourcePath: String,
    callerID: String,
    sessionID: String
  ) throws -> SkillState {
    let result = try authenticatedRequest(
      operation: "skill-import",
      payload: ["source_path": .string(sourcePath)],
      callerID: callerID,
      sessionID: sessionID
    )
    return try SkillState(result: result)
  }

  public func scanSkill(
    name: String,
    callerID: String,
    sessionID: String
  ) throws -> SkillScan {
    let result = try authenticatedRequest(
      operation: "skill-scan",
      payload: ["name": .string(name)],
      callerID: callerID,
      sessionID: sessionID
    )
    return try SkillScan(result: result)
  }

  public func setSkillEnabled(
    name: String,
    enabled: Bool,
    callerID: String,
    sessionID: String
  ) throws -> SkillState {
    let result = try authenticatedRequest(
      operation: enabled ? "skill-enable" : "skill-disable",
      payload: ["name": .string(name)],
      callerID: callerID,
      sessionID: sessionID
    )
    return try SkillState(result: result)
  }

  public func assistantRequest(
    text: String,
    callerID: String,
    sessionID: String,
    timezone: String = TimeZone.current.identifier
  ) throws -> AssistantResponse {
    let result = try authenticatedRequest(
      operation: "assistant-request",
      payload: [
        "text": .string(text),
        "input_mode": .string("typed"),
        "timezone": .string(timezone),
      ],
      callerID: callerID,
      sessionID: sessionID,
      responseTimeout: assistantResponseTimeoutSeconds
    )
    return try AssistantResponse(result: result)
  }

  public func automationStatus(
    callerID: String,
    sessionID: String
  ) throws -> AutomationStatus {
    let result = try authenticatedRequest(
      operation: "automation-status",
      payload: [:],
      callerID: callerID,
      sessionID: sessionID
    )
    return try AutomationStatus(value: result)
  }

  public func automationSchedules(
    callerID: String,
    sessionID: String
  ) throws -> [AutomationSchedule] {
    let result = try authenticatedRequest(
      operation: "automation-list",
      payload: [:],
      callerID: callerID,
      sessionID: sessionID
    )
    guard let values = result["jobs"]?.arrayValue else {
      throw RuntimeClientError.malformedResponse
    }
    return try values.map(AutomationSchedule.init(value:))
  }

  public func automationHistory(
    jobID: String? = nil,
    callerID: String,
    sessionID: String,
    limit: Int = 50
  ) throws -> [AutomationExecution] {
    var payload: [String: JSONValue] = ["limit": .integer(limit)]
    if let jobID { payload["job_id"] = .string(jobID) }
    let result = try authenticatedRequest(
      operation: "automation-history",
      payload: payload,
      callerID: callerID,
      sessionID: sessionID
    )
    guard let values = result["executions"]?.arrayValue else {
      throw RuntimeClientError.malformedResponse
    }
    return try values.map(AutomationExecution.init(value:))
  }

  public func createAutomationReminder(
    name: String,
    schedule: String,
    note: String,
    callerID: String,
    sessionID: String
  ) throws -> AutomationSchedule {
    let result = try authenticatedRequest(
      operation: "automation-create",
      payload: [
        "name": .string(name),
        "schedule": .string(schedule),
        "note": .string(note),
      ],
      callerID: callerID,
      sessionID: sessionID
    )
    guard let value = result["job"] else {
      throw RuntimeClientError.malformedResponse
    }
    return try AutomationSchedule(value: value)
  }

  public func requestAutomationActivation(
    jobID: String,
    requestID: String,
    callerID: String,
    sessionID: String
  ) throws -> ConsentChallenge {
    let result = try authenticatedRequest(
      operation: "automation-resume",
      payload: ["job_id": .string(jobID)],
      requestID: requestID,
      callerID: callerID,
      sessionID: sessionID
    )
    guard result["state"]?.stringValue == "awaiting_approval" else {
      throw RuntimeClientError.malformedResponse
    }
    return try ConsentChallenge(result: result)
  }

  public func pauseAutomationReminder(
    jobID: String,
    callerID: String,
    sessionID: String
  ) throws -> AutomationSchedule {
    let result = try authenticatedRequest(
      operation: "automation-pause",
      payload: ["job_id": .string(jobID)],
      callerID: callerID,
      sessionID: sessionID
    )
    guard let value = result["job"] else {
      throw RuntimeClientError.malformedResponse
    }
    return try AutomationSchedule(value: value)
  }

  public func removeAutomationReminder(
    jobID: String,
    callerID: String,
    sessionID: String
  ) throws -> Bool {
    let result = try authenticatedRequest(
      operation: "automation-remove",
      payload: ["job_id": .string(jobID)],
      callerID: callerID,
      sessionID: sessionID
    )
    guard let removed = result["removed"]?.boolValue else {
      throw RuntimeClientError.malformedResponse
    }
    return removed
  }

  public func stopAllAutomation(
    callerID: String,
    sessionID: String
  ) throws -> AutomationStatus {
    _ = try authenticatedRequest(
      operation: "automation-stop-all",
      payload: [:],
      callerID: callerID,
      sessionID: sessionID
    )
    return try automationStatus(callerID: callerID, sessionID: sessionID)
  }

  public func voiceStatus(callerID: String, sessionID: String) throws -> VoiceStatus {
    try voiceControl(operation: "voice-status", callerID: callerID, sessionID: sessionID)
  }

  public func startVoice(callerID: String, sessionID: String) throws -> VoiceStatus {
    try voiceControl(operation: "voice-start", callerID: callerID, sessionID: sessionID)
  }

  public func stopVoice(callerID: String, sessionID: String) throws -> VoiceStatus {
    try voiceControl(operation: "voice-stop", callerID: callerID, sessionID: sessionID)
  }

  public func startWake(callerID: String, sessionID: String) throws -> VoiceStatus {
    try voiceControl(operation: "wake-start", callerID: callerID, sessionID: sessionID)
  }

  public func stopWake(callerID: String, sessionID: String) throws -> VoiceStatus {
    try voiceControl(operation: "wake-stop", callerID: callerID, sessionID: sessionID)
  }

  public func testWakePhrase(
    _ phrase: String, callerID: String, sessionID: String
  ) throws -> VoiceStatus {
    try voiceControl(
      operation: "wake-test-start", payload: ["phrase": .string(phrase)],
      callerID: callerID, sessionID: sessionID
    )
  }

  public func setWakePhrase(
    _ phrase: String, callerID: String, sessionID: String
  ) throws -> VoiceStatus {
    try voiceControl(
      operation: "wake-phrase-set", payload: ["phrase": .string(phrase)],
      callerID: callerID, sessionID: sessionID
    )
  }

  public func voiceEvents(
    callerID: String,
    sessionID: String,
    after: Int = 0,
    limit: Int = 50
  ) throws -> [VoiceEvent] {
    let result = try authenticatedRequest(
      operation: "voice-events",
      payload: ["after": .integer(after), "limit": .integer(limit)],
      callerID: callerID,
      sessionID: sessionID
    )
    guard let values = result["events"]?.arrayValue else {
      throw RuntimeClientError.malformedResponse
    }
    return try values.map(VoiceEvent.init(value:))
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
      sessionID: sessionID,
      responseTimeout: executionTimeout
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
    let result = try submitConsentResult(
      challenge: challenge,
      decision: decision,
      signature: signature
    )
    guard let state = result["state"]?.stringValue else {
      throw RuntimeClientError.malformedResponse
    }
    return state
  }

  public func submitConsentResult(
    challenge: ConsentChallenge,
    decision: ConsentDecision,
    signature: String
  ) throws -> [String: JSONValue] {
    try request(
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
  }

  public func submitAutomationConsent(
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
        operation: "automation-consent-decision",
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
    sessionID: String,
    responseTimeout: TimeInterval? = nil
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
      ),
      responseTimeout: responseTimeout
    )
  }

  private func voiceControl(
    operation: String,
    payload: [String: JSONValue] = [:],
    callerID: String,
    sessionID: String
  ) throws -> VoiceStatus {
    let result = try authenticatedRequest(
      operation: operation,
      payload: payload,
      callerID: callerID,
      sessionID: sessionID
    )
    return try VoiceStatus(result: result)
  }

  private func request(
    endpoint: URL,
    envelope: RequestEnvelope,
    responseTimeout: TimeInterval? = nil
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
      timeout: responseTimeout ?? timeout,
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
