import AppKit
import Foundation
import JLAgentCore

@MainActor
final class AgentViewModel: ObservableObject {
  enum ConnectionState: String {
    case unavailable = "Runtime unavailable"
    case connecting = "Connecting"
    case connected = "Connected"
    case authenticationFailed = "Authentication failure"
    case degraded = "Degraded / misconfigured"
    case ready = "Ready"
  }

  @Published var connectionState: ConnectionState = .unavailable
  @Published var draft = RequestDraft()
  @Published var decision = "—"
  @Published var resultText = "No request sent."
  @Published var pendingConsent: ConsentChallenge?
  @Published var activity: [ActivityEvent] = []
  @Published var computerUseStatus: ComputerUseStatus?
  @Published var voiceStatus: VoiceStatus?
  @Published var voiceEvents: [VoiceEvent] = []
  @Published var voiceMessage = "Voice is off by default."
  @Published var runtimePID: Int?
  @Published var consentIdentityMatches = false
  @Published var isWorking = false

  let callerID = "native-macos-app"
  let sessionID = UUID().uuidString.lowercased()

  private let paths: RuntimePaths
  private let credentials: KeychainCredentialProvider
  private let signer: ConsentSigningKey
  private let client: JLRuntimeClient
  private let consentClock = ContinuousClock()
  private var activeRequestID: String?
  private var activeDraft: RequestDraft?
  private var pendingConsentDeadline: ContinuousClock.Instant?
  private var initialized = false

  init(paths: RuntimePaths = RuntimePaths()) {
    self.paths = paths
    let credentials = KeychainCredentialProvider()
    let signer = ConsentSigningKey()
    self.credentials = credentials
    self.signer = signer
    self.client = JLRuntimeClient(paths: paths, credentials: credentials)
  }

  func initialize() {
    guard !initialized else { return }
    initialized = true
    connectionState = .connecting
    let paths = paths
    let credentials = credentials
    let signer = signer
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    Task {
      do {
        let observed = try await Task.detached {
          _ = try signer.provisionPublicKey(at: paths.consentPublicKey)
          _ = try credentials.loadOrImport(from: paths.credential)
          return (
            try client.status(callerID: callerID, sessionID: sessionID),
            try signer.publicKeyFingerprint()
          )
        }.value
        apply(observed.0, localConsentFingerprint: observed.1)
        await refreshActivity()
        await refreshVoice()
      } catch {
        apply(error)
      }
    }
  }

  func refreshStatus() {
    connectionState = .connecting
    let client = client
    let signer = signer
    let callerID = callerID
    let sessionID = sessionID
    Task {
      do {
        let observed = try await Task.detached {
          (
            try client.status(callerID: callerID, sessionID: sessionID),
            try signer.publicKeyFingerprint()
          )
        }.value
        apply(observed.0, localConsentFingerprint: observed.1)
        await refreshVoice()
      } catch {
        apply(error)
      }
    }
  }

  func refreshCredential() {
    isWorking = true
    let credentials = credentials
    let credentialURL = paths.credential
    Task {
      do {
        _ = try await Task.detached {
          try credentials.refresh(from: credentialURL)
        }.value
        resultText = "Keychain credential refreshed."
        isWorking = false
        refreshStatus()
      } catch {
        resultText = display(error)
        isWorking = false
        apply(error)
      }
    }
  }

  func rotateConsentKey() {
    isWorking = true
    let signer = signer
    let publicKeyURL = paths.consentPublicKey
    Task {
      do {
        try await Task.detached {
          try signer.rotate(publicKeyURL: publicKeyURL)
        }.value
        consentIdentityMatches = false
        resultText = "Consent key rotated. Restart the runtime to trust it."
      } catch {
        resultText = display(error)
      }
      isWorking = false
    }
  }

  func sendRequest() {
    guard !isWorking else { return }
    pendingConsent = nil
    pendingConsentDeadline = nil
    isWorking = true
    decision = "Evaluating"
    resultText = "Preparing request…"
    let requestID = UUID().uuidString.lowercased()
    var observedDraft = draft
    captureForegroundContext(in: &observedDraft)
    let draft = observedDraft
    activeRequestID = requestID
    activeDraft = draft
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    Task {
      do {
        let prepared = try await Task.detached {
          try client.prepare(
            draft: draft,
            requestID: requestID,
            callerID: callerID,
            sessionID: sessionID
          )
        }.value
        if let challenge = prepared.challenge {
          decision = "CONFIRM"
          resultText = "Native consent is required."
          pendingConsentDeadline = ConsentExpiryPolicy.deadline(
            receivedAt: consentClock.now,
            expiresInSeconds: challenge.expiresInSeconds
          )
          pendingConsent = challenge
          isWorking = false
        } else {
          decision = "ALLOW"
          resultText = "Prepared; executing through the JL gate…"
          await executeActiveRequest()
        }
      } catch {
        handleRequestError(error)
      }
    }
  }

  func approve(_ challenge: ConsentChallenge) {
    decide(challenge, decision: .approve)
  }

  func reject(_ challenge: ConsentChallenge) {
    decide(challenge, decision: .reject)
  }

  func refreshActivity() async {
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    do {
      activity = try await Task.detached {
        try client.activity(callerID: callerID, sessionID: sessionID)
      }.value
    } catch {
      if activity.isEmpty { resultText = display(error) }
    }
  }

  func refreshVoice() async {
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    do {
      let observed = try await Task.detached {
        (
          try client.voiceStatus(callerID: callerID, sessionID: sessionID),
          try client.voiceEvents(callerID: callerID, sessionID: sessionID)
        )
      }.value
      voiceStatus = observed.0
      voiceEvents = observed.1
      voiceMessage = Self.voiceSummary(observed.0)
    } catch {
      voiceMessage = display(error)
    }
  }

  func startVoice() { updateVoice(.startVoice) }
  func stopVoice() { updateVoice(.stopVoice) }
  func startWake() { updateVoice(.startWake) }
  func stopWake() { updateVoice(.stopWake) }

  private enum VoiceOperation: Sendable {
    case startVoice, stopVoice, startWake, stopWake
  }

  private func updateVoice(_ operation: VoiceOperation) {
    guard !isWorking else { return }
    isWorking = true
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    Task {
      do {
        let status = try await Task.detached {
          switch operation {
          case .startVoice:
            try client.startVoice(callerID: callerID, sessionID: sessionID)
          case .stopVoice:
            try client.stopVoice(callerID: callerID, sessionID: sessionID)
          case .startWake:
            try client.startWake(callerID: callerID, sessionID: sessionID)
          case .stopWake:
            try client.stopWake(callerID: callerID, sessionID: sessionID)
          }
        }.value
        voiceStatus = status
        voiceMessage = Self.voiceSummary(status)
        isWorking = false
        await refreshVoice()
      } catch {
        voiceMessage = display(error)
        isWorking = false
      }
    }
  }

  private func decide(
    _ challenge: ConsentChallenge,
    decision consentDecision: ConsentDecision
  ) {
    guard !isWorking else { return }
    guard let deadline = pendingConsentDeadline,
      !ConsentExpiryPolicy.isExpired(deadline: deadline, now: consentClock.now)
    else {
      pendingConsent = nil
      pendingConsentDeadline = nil
      activeRequestID = nil
      activeDraft = nil
      decision = "DENY"
      resultText = "consent_expired: use Send / Prepare to create a new request."
      return
    }
    pendingConsent = nil
    pendingConsentDeadline = nil
    isWorking = true
    let signer = signer
    let client = client
    Task {
      do {
        let state = try await Task.detached {
          let signature = try signer.sign(
            challenge: challenge,
            decision: consentDecision
          )
          return try client.submitConsent(
            challenge: challenge,
            decision: consentDecision,
            signature: signature
          )
        }.value
        if consentDecision == .reject {
          decision = "DENY"
          resultText = "Request rejected. Nothing was executed."
          isWorking = false
          await refreshActivity()
        } else if state == "prepared" {
          decision = "ALLOW"
          resultText = "Consent accepted; executing through the JL gate…"
          await executeActiveRequest()
        } else {
          throw RuntimeClientError.malformedResponse
        }
      } catch {
        handleRequestError(error)
      }
    }
  }

  private func executeActiveRequest() async {
    guard let requestID = activeRequestID, var observedDraft = activeDraft else {
      handleRequestError(RuntimeClientError.invalidRequest("Prepared request was lost"))
      return
    }
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    captureForegroundContext(in: &observedDraft)
    let draft = observedDraft
    do {
      let result = try await Task.detached {
        try client.execute(
          draft: draft,
          requestID: requestID,
          callerID: callerID,
          sessionID: sessionID
        )
      }.value
      resultText = result.output ?? "Execution finished: \(result.state)"
      isWorking = false
      await refreshActivity()
    } catch {
      handleRequestError(error)
    }
  }

  private func apply(_ status: RuntimeStatus, localConsentFingerprint: String) {
    runtimePID = status.runtimePID
    computerUseStatus = status.computerUse
    consentIdentityMatches =
      status.consentEnrollmentCurrent
      && status.consentKeyFingerprint == localConsentFingerprint
    if !status.ready {
      connectionState = .unavailable
    } else if status.state == "degraded" || !status.consentAvailable
      || !consentIdentityMatches
    {
      connectionState = .degraded
      resultText = "Consent enrollment is unavailable or changed. Restart the foreground runtime."
    } else {
      connectionState = .ready
    }
  }

  private func apply(_ error: Error) {
    if !RuntimeStatusSnapshotPolicy.shouldRetain(after: error) {
      runtimePID = nil
      computerUseStatus = nil
      consentIdentityMatches = false
    }
    if case RuntimeClientError.server(let code, _) = error,
      code == "authentication_failed"
    {
      connectionState = .authenticationFailed
    } else if case RuntimeClientError.missingCredential = error {
      connectionState = .authenticationFailed
    } else if case RuntimeClientError.server = error {
      if connectionState == .connecting {
        connectionState = .connected
      }
    } else if case RuntimeClientError.malformedResponse = error {
      connectionState = .degraded
    } else if case RuntimeClientError.unsupportedProtocol = error {
      connectionState = .degraded
    } else {
      connectionState = .unavailable
    }
  }

  private func handleRequestError(_ error: Error) {
    if case RuntimeClientError.server(let code, _) = error,
      ["policy_denied", "approval_denied", "consent_rejected", "consent_expired"].contains(
        code)
    {
      decision = "DENY"
      if code == "consent_expired" {
        pendingConsent = nil
        pendingConsentDeadline = nil
        activeRequestID = nil
        activeDraft = nil
      }
    }
    resultText = display(error)
    isWorking = false
    apply(error)
    Task { await refreshActivity() }
  }

  private func display(_ error: Error) -> String {
    (error as? LocalizedError)?.errorDescription ?? "Request failed safely."
  }

  private func captureForegroundContext(in draft: inout RequestDraft) {
    guard draft.capabilityID == "core.hermes.computer-use",
      let action = try? JSONValue.parseObject(draft.argumentsJSON)["action"]?.stringValue,
      Self.mutatingComputerActions.contains(action)
    else { return }
    let application = NSWorkspace.shared.frontmostApplication
    draft.foregroundApp = application?.bundleIdentifier ?? application?.localizedName ?? ""
  }

  private static let mutatingComputerActions: Set<String> = [
    "click", "double_click", "right_click", "middle_click", "drag", "scroll",
    "type", "key", "set_value", "focus_app",
  ]

  var runtimePIDText: String {
    runtimePID.map(String.init) ?? "not running / unreachable"
  }

  private static func voiceSummary(_ status: VoiceStatus) -> String {
    if !status.enabled {
      return "Voice is disabled in the foreground runtime."
    }
    if !status.activationApproved {
      return "Voice activation awaits explicit dependency/model and Microphone approval."
    }
    if status.voice.active { return "Listening for a spoken turn. Tools are disabled." }
    if status.wake.active {
      return "Wake word armed: \(status.wake.phrase ?? "configured phrase")."
    }
    return "Voice is ready but not listening. Tools are disabled."
  }
}
