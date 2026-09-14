import Foundation

public let protocolVersion = 1
public let maximumMessageBytes = 64 * 1024
public let defaultTimeoutSeconds: TimeInterval = 2

public struct RequestEnvelope: Codable, Equatable, Sendable {
  public let protocolVersion: Int
  public let requestID: String
  public let callerID: String
  public let sessionID: String
  public let operation: String
  public let payload: [String: JSONValue]
  public let credential: String?

  enum CodingKeys: String, CodingKey {
    case protocolVersion = "protocol_version"
    case requestID = "request_id"
    case callerID = "caller_id"
    case sessionID = "session_id"
    case operation, payload, credential
  }

  public func encode(to encoder: Encoder) throws {
    var container = encoder.container(keyedBy: CodingKeys.self)
    try container.encode(protocolVersion, forKey: .protocolVersion)
    try container.encode(requestID, forKey: .requestID)
    try container.encode(callerID, forKey: .callerID)
    try container.encode(sessionID, forKey: .sessionID)
    try container.encode(operation, forKey: .operation)
    try container.encode(payload, forKey: .payload)
    if let credential {
      try container.encode(credential, forKey: .credential)
    } else {
      try container.encodeNil(forKey: .credential)
    }
  }
}

public struct ResponseError: Codable, Equatable, Sendable {
  public let code: String
  public let message: String
}

public struct ResponseEnvelope: Codable, Equatable, Sendable {
  public let protocolVersion: Int
  public let requestID: String
  public let ok: Bool
  public let result: [String: JSONValue]?
  public let error: ResponseError?

  enum CodingKeys: String, CodingKey {
    case protocolVersion = "protocol_version"
    case requestID = "request_id"
    case ok, result, error
  }
}

public struct RuntimeStatus: Equatable, Sendable {
  public let ready: Bool
  public let state: String
  public let runtimePID: Int
  public let transport: String
  public let hermesRevision: String
  public let consentAvailable: Bool
  public let consentKeyFingerprint: String?
  public let consentEnrollmentCurrent: Bool
  public let computerUse: ComputerUseStatus

  init(result: [String: JSONValue]) throws {
    guard
      let ready = result["ready"]?.boolValue,
      let state = result["state"]?.stringValue,
      let runtimePID = result["runtime_pid"]?.intValue,
      let transport = result["transport"]?.stringValue,
      let revision = result["hermes_revision"]?.stringValue,
      let consent = result["consent_available"]?.boolValue,
      let consentEnrollmentCurrent = result["consent_enrollment_current"]?.boolValue,
      let computerUse = result["computer_use"]?.objectValue
    else {
      throw RuntimeClientError.malformedResponse
    }
    self.ready = ready
    self.state = state
    self.runtimePID = runtimePID
    self.transport = transport
    self.hermesRevision = revision
    self.consentAvailable = consent
    self.consentKeyFingerprint = result["consent_key_fingerprint"]?.stringValue
    self.consentEnrollmentCurrent = consentEnrollmentCurrent
    self.computerUse = try ComputerUseStatus(value: computerUse)
  }
}

public struct MacOSPermissionStatus: Identifiable, Equatable, Sendable {
  public var id: String { kind }
  public let kind: String
  public let state: String
  public let explanation: String
  public let settingsURL: URL

  init(value: JSONValue) throws {
    guard
      let item = value.objectValue,
      Set(item.keys) == ["kind", "state", "explanation", "settings_url"],
      let kind = item["kind"]?.stringValue,
      let state = item["state"]?.stringValue,
      Self.allowedStates.contains(state),
      let explanation = item["explanation"]?.stringValue,
      let urlText = item["settings_url"]?.stringValue,
      let settingsURL = URL(string: urlText),
      settingsURL.scheme == "x-apple.systempreferences"
    else {
      throw RuntimeClientError.malformedResponse
    }
    self.kind = kind
    self.state = state
    self.explanation = explanation
    self.settingsURL = settingsURL
  }

  private static let allowedStates: Set<String> = [
    "granted", "denied", "notDetermined", "unknown", "unavailable",
    "restartRequired",
  ]
}

public struct ComputerUseStatus: Equatable, Sendable {
  public let enabled: Bool
  public let health: String
  public let ready: Bool
  public let platformSupported: Bool
  public let driverAvailable: Bool
  public let driverReachable: Bool
  public let driverContractReady: Bool
  public let driverVersion: String?
  public let driverAppAvailable: Bool
  public let driverIdentityReady: Bool
  public let driverBundleID: String?
  public let driverTeamID: String?
  public let hermesPinValid: Bool
  public let authenticatedRuntime: Bool
  public let policyReady: Bool
  public let consentReady: Bool
  public let driverServiceRequired: Bool
  public let executionReady: Bool
  public let blockedReason: String
  public let detail: String
  public let permissions: [MacOSPermissionStatus]

  init(value: [String: JSONValue]) throws {
    guard
      Set(value.keys) == [
        "enabled", "health", "ready", "platform_supported", "driver_available",
        "driver_reachable", "driver_contract_ready", "driver_version",
        "driver_app_available", "driver_identity_ready", "driver_bundle_id",
        "driver_team_id", "hermes_pin_valid", "authenticated_runtime",
        "policy_ready", "consent_ready", "driver_service_required",
        "execution_ready", "blocked_reason", "detail", "permissions",
      ],
      let enabled = value["enabled"]?.boolValue,
      let health = value["health"]?.stringValue,
      let ready = value["ready"]?.boolValue,
      let platform = value["platform_supported"]?.boolValue,
      let driver = value["driver_available"]?.boolValue,
      let reachable = value["driver_reachable"]?.boolValue,
      let contract = value["driver_contract_ready"]?.boolValue,
      let appAvailable = value["driver_app_available"]?.boolValue,
      let identityReady = value["driver_identity_ready"]?.boolValue,
      let hermesPin = value["hermes_pin_valid"]?.boolValue,
      let authenticatedRuntime = value["authenticated_runtime"]?.boolValue,
      let policyReady = value["policy_ready"]?.boolValue,
      let consentReady = value["consent_ready"]?.boolValue,
      let serviceRequired = value["driver_service_required"]?.boolValue,
      let executionReady = value["execution_ready"]?.boolValue,
      let blockedReason = value["blocked_reason"]?.stringValue,
      let detail = value["detail"]?.stringValue,
      let permissions = value["permissions"]?.arrayValue
    else {
      throw RuntimeClientError.malformedResponse
    }
    self.enabled = enabled
    self.health = health
    self.ready = ready
    self.platformSupported = platform
    self.driverAvailable = driver
    self.driverReachable = reachable
    self.driverContractReady = contract
    self.driverVersion = value["driver_version"]?.stringValue
    self.driverAppAvailable = appAvailable
    self.driverIdentityReady = identityReady
    self.driverBundleID = value["driver_bundle_id"]?.stringValue
    self.driverTeamID = value["driver_team_id"]?.stringValue
    self.hermesPinValid = hermesPin
    self.authenticatedRuntime = authenticatedRuntime
    self.policyReady = policyReady
    self.consentReady = consentReady
    self.driverServiceRequired = serviceRequired
    self.executionReady = executionReady
    self.blockedReason = blockedReason
    self.detail = detail
    self.permissions = try permissions.map(MacOSPermissionStatus.init(value:))
  }
}

public struct ActivityEvent: Identifiable, Equatable, Sendable {
  public var id: String { "\(timestamp)|\(capabilityID)|\(executionStatus)" }
  public let timestamp: String
  public let capabilityID: String
  public let actionClasses: [String]
  public let policyDecision: String
  public let executionStatus: String

  init(value: JSONValue) throws {
    guard
      let item = value.objectValue,
      Set(item.keys).isSubset(of: Self.allowedKeys),
      let timestamp = item["timestamp"]?.stringValue,
      let capability = item["capability_id"]?.stringValue,
      let classes = item["action_class"]?.arrayValue,
      let policy = item["policy_decision"]?.stringValue,
      let status = item["execution_status"]?.stringValue
    else {
      throw RuntimeClientError.malformedResponse
    }
    self.timestamp = timestamp
    self.capabilityID = capability
    self.actionClasses = try classes.map {
      guard let value = $0.stringValue else {
        throw RuntimeClientError.malformedResponse
      }
      return value
    }
    self.policyDecision = policy
    self.executionStatus = status
  }

  private static let allowedKeys: Set<String> = [
    "timestamp", "capability_id", "action_class", "policy_decision",
    "execution_status",
  ]
}

public struct ConsentChallenge: Identifiable, Equatable, Sendable {
  public var id: String { consentID }
  public let consentID: String
  public let requestID: String
  public let callerID: String
  public let sessionID: String
  public let nonce: String
  public let capabilityID: String
  public let action: String
  public let actionClasses: [String]
  public let targetSummary: String
  public let riskLevel: String
  public let expiresInSeconds: Int

  public init(
    consentID: String,
    requestID: String,
    callerID: String,
    sessionID: String,
    nonce: String,
    capabilityID: String,
    action: String,
    actionClasses: [String],
    targetSummary: String,
    riskLevel: String,
    expiresInSeconds: Int
  ) {
    self.consentID = consentID
    self.requestID = requestID
    self.callerID = callerID
    self.sessionID = sessionID
    self.nonce = nonce
    self.capabilityID = capabilityID
    self.action = action
    self.actionClasses = actionClasses
    self.targetSummary = targetSummary
    self.riskLevel = riskLevel
    self.expiresInSeconds = expiresInSeconds
  }

  init(result: [String: JSONValue]) throws {
    guard
      let consent = result["consent"]?.objectValue,
      let consentID = consent["consent_id"]?.stringValue,
      let requestID = consent["request_id"]?.stringValue,
      let callerID = consent["caller_id"]?.stringValue,
      let sessionID = consent["session_id"]?.stringValue,
      let nonce = consent["nonce"]?.stringValue,
      let capabilityID = consent["capability_id"]?.stringValue,
      let action = consent["action"]?.stringValue,
      let actionClasses = consent["action_classes"]?.arrayValue,
      let targetSummary = consent["target_summary"]?.stringValue,
      let riskLevel = consent["risk_level"]?.stringValue,
      let expires = consent["expires_in_seconds"]?.intValue
    else {
      throw RuntimeClientError.malformedResponse
    }
    self.consentID = consentID
    self.requestID = requestID
    self.callerID = callerID
    self.sessionID = sessionID
    self.nonce = nonce
    self.capabilityID = capabilityID
    self.action = action
    self.actionClasses = try actionClasses.map {
      guard let value = $0.stringValue else {
        throw RuntimeClientError.malformedResponse
      }
      return value
    }
    self.targetSummary = targetSummary
    self.riskLevel = riskLevel
    self.expiresInSeconds = expires
  }
}

public enum ConsentDecision: String, Sendable {
  case approve
  case reject
}

public struct RequestDraft: Equatable, Sendable {
  public var capabilityID = "core.hermes.files"
  public var action = "read_file"
  public var argumentsJSON = "{\"path\":\"docs/ARCHITECTURE.md\"}"
  public var requestedPermission = "local.read"
  public var resolvedTarget = "docs/ARCHITECTURE.md"
  public var foregroundApp = ""
  public var targetWithinWorkspace = true
  public var reversible = true

  public init() {}

  func payload() throws -> [String: JSONValue] {
    let arguments = try JSONValue.parseObject(argumentsJSON)
    return [
      "capability_id": .string(capabilityID),
      "action": .object([
        "action": .string(action),
        "normalized_arguments": .object(arguments),
        "requested_permissions": .array(
          requestedPermission.split(separator: ",").map {
            .string($0.trimmingCharacters(in: .whitespaces))
          }
        ),
        "resolved_target": .string(resolvedTarget),
        "foreground_app": .string(foregroundApp),
        "target_within_workspace": .bool(targetWithinWorkspace),
        "reversible": .bool(reversible),
        "approval_surface_available": .bool(true),
      ]),
      "route": .object([
        "category": .string("simple"),
        "required_abilities": .array([
          .string("text"), .string("tool-calling"),
        ]),
        "local_only": .bool(true),
        "off_device_allowed": .bool(false),
      ]),
      "candidates": .array([
        .object([
          "id": .string("native.validation.local"),
          "provider": .string("native-validation"),
          "model": .string("deterministic-boundary"),
          "abilities": .array([
            .string("text"), .string("tool-calling"),
          ]),
          "health": .string("healthy"),
          "local": .bool(true),
          "data_residency": .string("device"),
          "cost_class": .integer(0),
        ])
      ]),
    ]
  }
}
