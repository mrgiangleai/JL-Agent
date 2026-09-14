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
  @Published var isWorking = false

  let callerID = "native-macos-app"
  let sessionID = UUID().uuidString.lowercased()

  private let paths: RuntimePaths
  private let credentials: KeychainCredentialProvider
  private let signer: ConsentSigningKey
  private let client: JLRuntimeClient
  private var activeRequestID: String?
  private var activeDraft: RequestDraft?
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
        let status = try await Task.detached {
          _ = try signer.provisionPublicKey(at: paths.consentPublicKey)
          _ = try credentials.loadOrImport(from: paths.credential)
          return try client.status(callerID: callerID, sessionID: sessionID)
        }.value
        apply(status)
        await refreshActivity()
      } catch {
        apply(error)
      }
    }
  }

  func refreshStatus() {
    connectionState = .connecting
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    Task {
      do {
        let status = try await Task.detached {
          try client.status(callerID: callerID, sessionID: sessionID)
        }.value
        apply(status)
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
        resultText = "Consent key rotated. Restart the runtime to trust it."
      } catch {
        resultText = display(error)
      }
      isWorking = false
    }
  }

  func sendRequest() {
    guard !isWorking else { return }
    isWorking = true
    decision = "Evaluating"
    resultText = "Preparing request…"
    let requestID = UUID().uuidString.lowercased()
    let draft = draft
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

  private func decide(
    _ challenge: ConsentChallenge,
    decision consentDecision: ConsentDecision
  ) {
    guard !isWorking else { return }
    pendingConsent = nil
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
    guard let requestID = activeRequestID, let draft = activeDraft else {
      handleRequestError(RuntimeClientError.invalidRequest("Prepared request was lost"))
      return
    }
    let client = client
    let callerID = callerID
    let sessionID = sessionID
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

  private func apply(_ status: RuntimeStatus) {
    computerUseStatus = status.computerUse
    if !status.ready {
      connectionState = .unavailable
    } else if status.state == "degraded" || !status.consentAvailable {
      connectionState = .degraded
      resultText = "Consent key is not loaded. Restart the foreground runtime."
    } else {
      connectionState = .ready
    }
  }

  private func apply(_ error: Error) {
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
      ["policy_denied", "approval_denied", "consent_rejected"].contains(code)
    {
      decision = "DENY"
    }
    resultText = display(error)
    isWorking = false
    apply(error)
    Task { await refreshActivity() }
  }

  private func display(_ error: Error) -> String {
    (error as? LocalizedError)?.errorDescription ?? "Request failed safely."
  }
}
