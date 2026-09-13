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
  public let transport: String
  public let hermesRevision: String
  public let consentAvailable: Bool

  init(result: [String: JSONValue]) throws {
    guard
      let ready = result["ready"]?.boolValue,
      let state = result["state"]?.stringValue,
      let transport = result["transport"]?.stringValue,
      let revision = result["hermes_revision"]?.stringValue,
      let consent = result["consent_available"]?.boolValue
    else {
      throw RuntimeClientError.malformedResponse
    }
    self.ready = ready
    self.state = state
    self.transport = transport
    self.hermesRevision = revision
    self.consentAvailable = consent
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
        "requested_permissions": .array([.string(requestedPermission)]),
        "resolved_target": .string(resolvedTarget),
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
