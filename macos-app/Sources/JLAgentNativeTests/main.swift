import Darwin
import Foundation
import JLAgentCore
import Security

private struct TestFailure: Error, CustomStringConvertible {
  let description: String
}

private struct StaticCredential: CredentialProviding {
  let value: String?

  func loadCredential() throws -> String {
    guard let value else { throw RuntimeClientError.missingCredential }
    return value
  }
}

private final class StubTransport: IPCTransport, @unchecked Sendable {
  let handler: @Sendable (Data) throws -> Data
  private var callCount = 0
  private let lock = NSLock()

  init(handler: @escaping @Sendable (Data) throws -> Data) {
    self.handler = handler
  }

  var calls: Int { lock.withLock { callCount } }

  func send(
    endpoint: URL,
    data: Data,
    timeout: TimeInterval,
    maximumResponseBytes: Int
  ) throws -> Data {
    lock.withLock { callCount += 1 }
    return try handler(data)
  }
}

@main
enum NativeContractTests {
  private static let paths = RuntimePaths(
    root: URL(fileURLWithPath: "/tmp/jl-agent-native-tests")
  )

  static func main() throws {
    if CommandLine.arguments.count > 1 {
      try runIntegrationCommand(Array(CommandLine.arguments.dropFirst()))
      return
    }
    try missingCredentialFailsBeforeTransport()
    try authenticationFailureIsStructured()
    try malformedAndUnsupportedResponsesFailSafely()
    try consentPayloadCannotCarryWildcardApproval()
    try activityRejectsSensitiveFields()
    try computerUsePermissionStatusIsStructured()
    try keychainCredentialImportsAndRefreshes()
    try consentKeySignsWithoutExportingPrivateMaterial()
    print("8 native contract tests passed")
  }

  private static func runIntegrationCommand(_ arguments: [String]) throws {
    guard arguments.count == 4 else {
      throw TestFailure(
        description: "integration command requires mode, runtime root, service, tag"
      )
    }
    let mode = arguments[0]
    let paths = RuntimePaths(root: URL(fileURLWithPath: arguments[1]))
    let credentials = KeychainCredentialProvider(
      service: arguments[2], account: "native-ipc-smoke"
    )
    let signer = ConsentSigningKey(tag: arguments[3])
    if mode == "provision" {
      _ = try credentials.loadOrImport(from: paths.credential)
      _ = try signer.provisionPublicKey(at: paths.consentPublicKey)
      print("native IPC smoke credentials provisioned")
      return
    }
    if mode == "cleanup" {
      try credentials.remove()
      try signer.remove()
      print("native IPC smoke Keychain items removed")
      return
    }
    guard mode == "smoke" else {
      throw TestFailure(description: "unknown integration command")
    }

    let client = JLRuntimeClient(paths: paths, credentials: credentials)
    let caller = "native-ipc-smoke"
    let session = "native-ipc-smoke-session"
    let status = try client.status(callerID: caller, sessionID: session)
    try check(status.ready && status.consentAvailable, "runtime was not consent-ready")
    var draft = RequestDraft()
    draft.action = "write_file"
    draft.argumentsJSON = "{\"path\":\"/outside/native-smoke\",\"content\":\"safe\"}"
    draft.requestedPermission = "local.write.reversible"
    draft.resolvedTarget = "/outside/native-smoke"
    draft.targetWithinWorkspace = false
    let requestID = "native-ipc-smoke-request"
    let prepared = try client.prepare(
      draft: draft,
      requestID: requestID,
      callerID: caller,
      sessionID: session
    )
    let pending = try require(prepared.challenge, "runtime did not request consent")
    let signature = try signer.sign(challenge: pending, decision: .approve)
    let consentState = try client.submitConsent(
      challenge: pending,
      decision: .approve,
      signature: signature
    )
    try check(consentState == "prepared", "trusted consent did not prepare")
    let result = try client.execute(
      draft: draft,
      requestID: requestID,
      callerID: caller,
      sessionID: session
    )
    try check(result.state == "completed", "execution did not complete")
    try check(result.output == "native-ipc-smoke-ok", "unexpected fake output")
    let activity = try client.activity(callerID: caller, sessionID: session)
    try check(!activity.isEmpty, "safe activity was empty")
    print("native protocol-v1 AF_UNIX consent smoke passed")
  }

  private static func missingCredentialFailsBeforeTransport() throws {
    let transport = StubTransport { _ in
      throw TestFailure(description: "transport ran without a credential")
    }
    let client = JLRuntimeClient(
      paths: paths,
      credentials: StaticCredential(value: nil),
      transport: transport
    )
    try expect(.missingCredential) {
      try client.prepare(
        draft: RequestDraft(),
        requestID: "request-1",
        callerID: "native-app",
        sessionID: "session-1"
      )
    }
    try check(transport.calls == 0, "missing credential reached transport")
  }

  private static func authenticationFailureIsStructured() throws {
    let transport = StubTransport { request in
      try response(
        for: request,
        ok: false,
        result: nil,
        error: ["code": "authentication_failed", "message": "denied"]
      )
    }
    let client = makeClient(transport: transport)
    try expect(.server(code: "authentication_failed", message: "denied")) {
      try prepare(client)
    }
  }

  private static func malformedAndUnsupportedResponsesFailSafely() throws {
    let malformed = StubTransport { _ in Data("[]".utf8) }
    try expect(.malformedResponse) { try prepare(makeClient(transport: malformed)) }

    let unsupported = StubTransport { request in
      try response(
        for: request,
        ok: true,
        result: ["state": "prepared"],
        error: nil,
        protocolVersion: 2
      )
    }
    try expect(.unsupportedProtocol(2)) {
      try prepare(makeClient(transport: unsupported))
    }

    let mismatch = StubTransport { _ in
      try JSONSerialization.data(withJSONObject: [
        "protocol_version": 1,
        "request_id": "other",
        "ok": true,
        "result": ["state": "prepared"],
        "error": NSNull(),
      ])
    }
    try expect(.mismatchedResponse) {
      try prepare(makeClient(transport: mismatch))
    }
  }

  private static func consentPayloadCannotCarryWildcardApproval() throws {
    let challenge = challenge()
    let transport = StubTransport { request in
      let object = try require(
        JSONSerialization.jsonObject(with: request) as? [String: Any],
        "consent envelope is malformed"
      )
      try check(
        Set(object.keys) == [
          "protocol_version", "request_id", "caller_id", "session_id",
          "operation", "payload", "credential",
        ],
        "consent envelope omitted protocol fields"
      )
      try check(object["credential"] is NSNull, "consent credential was not null")
      let payload = try require(
        object["payload"] as? [String: Any],
        "consent payload is malformed"
      )
      try check(
        Set(payload.keys) == ["consent_id", "decision", "signature"],
        "consent payload exposed approval authority"
      )
      try check(payload["binding_fingerprint"] == nil, "fingerprint was sent")
      try check(payload["approval_id"] == nil, "approval ID was sent")
      return try response(
        for: request,
        ok: true,
        result: ["state": "prepared"],
        error: nil
      )
    }
    let state = try makeClient(transport: transport).submitConsent(
      challenge: challenge,
      decision: .approve,
      signature: "signed-exact-challenge"
    )
    try check(state == "prepared", "consent response state was not prepared")
  }

  private static func activityRejectsSensitiveFields() throws {
    let transport = StubTransport { request in
      try response(
        for: request,
        ok: true,
        result: [
          "events": [
            [
              "timestamp": "2026-09-13T00:00:00Z",
              "capability_id": "core.hermes.files",
              "action_class": ["read-only"],
              "policy_decision": "may-proceed",
              "execution_status": "completed",
              "credential": "must-not-pass",
            ]
          ]
        ],
        error: nil
      )
    }
    try expect(.malformedResponse) {
      try makeClient(transport: transport).activity(
        callerID: "native-app", sessionID: "session-1"
      )
    }
  }

  private static func computerUsePermissionStatusIsStructured() throws {
    let transport = StubTransport { request in
      try response(
        for: request,
        ok: true,
        result: [
          "ready": true,
          "state": "ready",
          "transport": "AF_UNIX",
          "hermes_revision": String(repeating: "a", count: 40),
          "consent_available": true,
          "computer_use": [
            "enabled": true,
            "health": "unavailable",
            "ready": false,
            "platform_supported": true,
            "driver_available": true,
            "driver_contract_ready": true,
            "driver_version": "0.20.1",
            "detail": "required permission is unknown",
            "permissions": [
              [
                "kind": "accessibility",
                "state": "unknown",
                "explanation": "Required for input.",
                "settings_url":
                  "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
              ]
            ],
          ],
        ],
        error: nil
      )
    }

    let status = try makeClient(transport: transport).status(
      callerID: "native-app", sessionID: "session-1"
    )
    try check(!status.computerUse.ready, "missing TCC was shown as ready")
    try check(
      status.computerUse.permissions.first?.state == "unknown",
      "permission state was not preserved"
    )
  }

  private static func keychainCredentialImportsAndRefreshes() throws {
    let provider = KeychainCredentialProvider(
      service: "com.jlagent.tests.\(UUID().uuidString)",
      account: "test"
    )
    defer { try? provider.remove() }
    let directory = FileManager.default.temporaryDirectory
      .appendingPathComponent("jl-agent-keychain-\(UUID().uuidString)")
    try FileManager.default.createDirectory(
      at: directory,
      withIntermediateDirectories: false,
      attributes: [.posixPermissions: 0o700]
    )
    defer { try? FileManager.default.removeItem(at: directory) }
    let source = directory.appendingPathComponent("ipc.credential")
    try Data(String(repeating: "a", count: 43).utf8).write(to: source)
    try check(chmod(source.path, 0o600) == 0, "credential chmod failed")
    let imported = try provider.loadOrImport(from: source)
    try check(
      imported == String(repeating: "a", count: 43),
      "credential import failed"
    )
    try Data(String(repeating: "b", count: 43).utf8).write(to: source)
    try check(chmod(source.path, 0o600) == 0, "credential refresh chmod failed")
    _ = try provider.refresh(from: source)
    let refreshed = try provider.loadCredential()
    try check(
      refreshed == String(repeating: "b", count: 43),
      "credential refresh failed"
    )
  }

  private static func consentKeySignsWithoutExportingPrivateMaterial() throws {
    let key = ConsentSigningKey(tag: "com.jlagent.tests.\(UUID().uuidString)")
    defer { try? key.remove() }
    let directory = FileManager.default.temporaryDirectory
      .appendingPathComponent("jl-agent-consent-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: directory) }
    let publicURL = directory.appendingPathComponent("public.der")
    let publicData = try key.provisionPublicKey(at: publicURL)
    let item = challenge()
    let signature = try require(
      Data(base64Encoded: try key.sign(challenge: item, decision: .approve)),
      "signature is not base64"
    )
    let attributes: [String: Any] = [
      kSecAttrKeyType as String: kSecAttrKeyTypeRSA,
      kSecAttrKeyClass as String: kSecAttrKeyClassPublic,
      kSecAttrKeySizeInBits as String: 2048,
    ]
    let publicKey = try require(
      SecKeyCreateWithData(publicData as CFData, attributes as CFDictionary, nil),
      "public key could not be decoded"
    )
    try check(
      SecKeyVerifySignature(
        publicKey,
        .rsaSignatureMessagePKCS1v15SHA256,
        canonicalConsentMessage(challenge: item, decision: .approve) as CFData,
        signature as CFData,
        nil
      ),
      "consent signature did not verify"
    )
    let mode = try require(
      FileManager.default.attributesOfItem(atPath: publicURL.path)[
        .posixPermissions
      ] as? NSNumber,
      "public key mode is unavailable"
    )
    try check(mode.intValue == 0o600, "public key file is not private")
  }

  private static func makeClient(
    transport: StubTransport,
    credential: String = String(repeating: "c", count: 32)
  ) -> JLRuntimeClient {
    JLRuntimeClient(
      paths: paths,
      credentials: StaticCredential(value: credential),
      transport: transport
    )
  }

  private static func prepare(_ client: JLRuntimeClient) throws -> PrepareResult {
    try client.prepare(
      draft: RequestDraft(),
      requestID: "request-1",
      callerID: "native-app",
      sessionID: "session-1"
    )
  }

  private static func challenge() -> ConsentChallenge {
    ConsentChallenge(
      consentID: "consent-1",
      requestID: "request-1",
      callerID: "native-app",
      sessionID: "session-1",
      nonce: "nonce-1",
      capabilityID: "core.hermes.files",
      action: "write_file",
      actionClasses: ["reversible-local"],
      targetSummary: "/outside/file",
      riskLevel: "medium",
      expiresInSeconds: 30
    )
  }

  private static func response(
    for request: Data,
    ok: Bool,
    result: [String: Any]?,
    error: [String: String]?,
    protocolVersion: Int = 1
  ) throws -> Data {
    let envelope = try require(
      JSONSerialization.jsonObject(with: request) as? [String: Any],
      "request envelope is malformed"
    )
    return try JSONSerialization.data(withJSONObject: [
      "protocol_version": protocolVersion,
      "request_id": envelope["request_id"] as Any,
      "ok": ok,
      "result": result ?? NSNull(),
      "error": error ?? NSNull(),
    ])
  }

  private static func expect<T>(
    _ expected: RuntimeClientError,
    _ operation: () throws -> T
  ) throws {
    do {
      _ = try operation()
    } catch let error as RuntimeClientError {
      try check(error == expected, "expected \(expected), received \(error)")
      return
    }
    throw TestFailure(description: "expected \(expected) to be thrown")
  }
}

private func check(_ condition: @autoclosure () -> Bool, _ message: String) throws {
  if !condition() { throw TestFailure(description: message) }
}

private func require<T>(_ value: T?, _ message: String) throws -> T {
  guard let value else { throw TestFailure(description: message) }
  return value
}
