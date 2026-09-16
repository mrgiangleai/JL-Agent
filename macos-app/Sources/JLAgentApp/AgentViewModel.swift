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
  @Published var assistantText = ""
  @Published var assistantState = "No request sent."
  @Published var assistantResultText = "No request sent."
  @Published var pendingAssistantConsent: ConsentChallenge?
  @Published var draft = RequestDraft()
  @Published var decision = "—"
  @Published var resultText = "No request sent."
  @Published var pendingConsent: ConsentChallenge?
  @Published var activity: [ActivityEvent] = []
  @Published var skills: [ManagedSkill] = []
  @Published var selectedSkillName: String?
  @Published var skillPreview: SkillPreview?
  @Published var skillScan: SkillScan?
  @Published var skillMessage = "Skills have not been checked."
  @Published var isSkillWorking = false
  @Published var computerUseStatus: ComputerUseStatus?
  @Published var voiceStatus: VoiceStatus?
  @Published var voiceEvents: [VoiceEvent] = []
  @Published var voiceMessage = "Voice is off by default."
  @Published var automationStatus: AutomationStatus?
  @Published var schedules: [AutomationSchedule] = []
  @Published var scheduleHistory: [AutomationExecution] = []
  @Published var automationMessage = "Scheduler status has not been checked."
  @Published var reminderName = "Local reminder"
  @Published var reminderNote = "JL reminder"
  @Published var reminderScheduleText = "5m"
  @Published var reminderRecurring = false
  @Published var pendingAutomationConsent: ConsentChallenge?
  @Published var wakePhraseDraft = "HEY J L"
  @Published var testedWakePhrase: String?
  @Published var wakePhraseMessage = "Test a phrase before making it the default."
  @Published var runtimePID: Int?
  @Published var runtimeMessage = "Starting the packaged JL runtime…"
  @Published var consentIdentityMatches = false
  @Published var isWorking = false

  let callerID = "native-macos-app"
  let sessionID = UUID().uuidString.lowercased()

  private let paths: RuntimePaths
  private let credentials: KeychainCredentialProvider
  private let signer: ConsentSigningKey
  private let client: JLRuntimeClient
  private let runtimeProcess: RuntimeProcessController
  private let consentClock = ContinuousClock()
  private var activeRequestID: String?
  private var activeDraft: RequestDraft?
  private var pendingAssistantConsentDeadline: ContinuousClock.Instant?
  private var pendingConsentDeadline: ContinuousClock.Instant?
  private var pendingAutomationConsentDeadline: ContinuousClock.Instant?
  private var initialized = false

  init(paths: RuntimePaths = RuntimePaths()) {
    self.paths = paths
    let credentials = KeychainCredentialProvider()
    let signer = ConsentSigningKey()
    self.credentials = credentials
    self.signer = signer
    self.client = JLRuntimeClient(paths: paths, credentials: credentials)
    self.runtimeProcess = RuntimeProcessController(paths: paths)
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
        try await runtimeProcess.ensureReady()
        let observed = try await Task.detached {
          _ = try signer.provisionPublicKey(at: paths.consentPublicKey)
          _ = try credentials.loadOrImport(from: paths.credential)
          return (
            try client.status(callerID: callerID, sessionID: sessionID),
            try signer.publicKeyFingerprint()
          )
        }.value
        apply(observed.0, localConsentFingerprint: observed.1)
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
        try await runtimeProcess.ensureReady()
        let observed = try await Task.detached {
          (
            try client.status(callerID: callerID, sessionID: sessionID),
            try signer.publicKeyFingerprint()
          )
        }.value
        apply(observed.0, localConsentFingerprint: observed.1)
      } catch {
        apply(error)
      }
    }
  }

  func restartRuntime() {
    guard !isWorking else { return }
    isWorking = true
    runtimeMessage = "Restarting the packaged JL runtime…"
    Task {
      do {
        try await runtimeProcess.restart()
        isWorking = false
        runtimeMessage = "JL runtime restarted safely."
        refreshStatus()
      } catch {
        isWorking = false
        apply(error)
      }
    }
  }

  func stopRuntime() {
    guard !isWorking else { return }
    let stopped = runtimeProcess.stopOwnedRuntime()
    runtimePID = nil
    connectionState = .unavailable
    runtimeMessage = stopped
      ? "JL runtime stopped. Use Refresh Status to start it again."
      : "JL runtime is not owned by this app and was left running."
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

  func sendAssistantRequest() {
    guard !isWorking else { return }
    let text = assistantText.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !text.isEmpty else {
      assistantState = "malformed_payload"
      assistantResultText = "Text must be non-empty."
      return
    }
    pendingAssistantConsent = nil
    pendingAssistantConsentDeadline = nil
    isWorking = true
    assistantState = "sending"
    assistantResultText = "Sending typed assistant request..."
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    let timezone = TimeZone.current.identifier
    Task {
      do {
        let response = try await Task.detached {
          try client.assistantRequest(
            text: text,
            callerID: callerID,
            sessionID: sessionID,
            timezone: timezone
          )
        }.value
        assistantState = response.state
        assistantResultText = response.resultJSON
        if let challenge = response.consent {
          pendingAssistantConsentDeadline = ConsentExpiryPolicy.deadline(
            receivedAt: consentClock.now,
            expiresInSeconds: challenge.expiresInSeconds
          )
          pendingAssistantConsent = challenge
        }
        isWorking = false
      } catch {
        handleAssistantError(error)
      }
    }
  }

  func approveAssistant(_ challenge: ConsentChallenge) {
    decideAssistant(challenge, decision: .approve)
  }

  func rejectAssistant(_ challenge: ConsentChallenge) {
    decideAssistant(challenge, decision: .reject)
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

  func refreshSkills() async {
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    do {
      let page = try await Task.detached {
        try client.skillsList(callerID: callerID, sessionID: sessionID)
      }.value
      skills = page.skills
      skillMessage = page.skills.isEmpty
        ? "No Hermes-managed skills installed."
        : "\(page.count) managed skill\(page.count == 1 ? "" : "s"). Installed skills remain outside normal conversation context."
    } catch {
      skillMessage = display(error)
    }
  }

  func importSkill(from url: URL) {
    guard !isSkillWorking else { return }
    guard url.hasDirectoryPath || url.lastPathComponent == "SKILL.md" else {
      skillMessage = "Choose a skill folder or its root SKILL.md file."
      return
    }
    let path = url.path
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    isSkillWorking = true
    skillMessage = "Importing: quarantine and Hermes Skills Guard scan in progress…"
    Task {
      do {
        let state = try await Task.detached {
          try client.importSkill(
            sourcePath: path,
            callerID: callerID,
            sessionID: sessionID
          )
        }.value
        skillMessage = "Imported \(state.name). Hermes installed it Disabled by default."
        skillPreview = nil
        skillScan = nil
        await refreshSkills()
      } catch {
        skillMessage = display(error)
      }
      isSkillWorking = false
    }
  }

  func previewSkill(_ skill: ManagedSkill) {
    guard !isSkillWorking else { return }
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    let name = skill.name
    selectedSkillName = name
    isSkillWorking = true
    skillMessage = "Reading a bounded Hermes preview for \(name)…"
    Task {
      do {
        skillPreview = try await Task.detached {
          try client.skillPreview(
            name: name,
            maxChars: 2_048,
            callerID: callerID,
            sessionID: sessionID
          )
        }.value
        skillMessage = "Preview is bounded to 2,048 characters; no skill execution occurred."
      } catch {
        skillMessage = display(error)
      }
      isSkillWorking = false
    }
  }

  func scanSkill(_ skill: ManagedSkill) {
    guard !isSkillWorking else { return }
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    let name = skill.name
    selectedSkillName = name
    isSkillWorking = true
    skillMessage = "Running Hermes Skills Guard for \(name)…"
    Task {
      do {
        skillScan = try await Task.detached {
          try client.scanSkill(name: name, callerID: callerID, sessionID: sessionID)
        }.value
        skillMessage = "Hermes scan completed for \(name)."
      } catch {
        skillMessage = display(error)
      }
      isSkillWorking = false
    }
  }

  func setSkill(_ skill: ManagedSkill, enabled: Bool) {
    guard !isSkillWorking else { return }
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    let name = skill.name
    isSkillWorking = true
    skillMessage = enabled
      ? "Enabling \(name) visibility through Hermes…"
      : "Disabling \(name) visibility through Hermes…"
    Task {
      do {
        let state = try await Task.detached {
          try client.setSkillEnabled(
            name: name,
            enabled: enabled,
            callerID: callerID,
            sessionID: sessionID
          )
        }.value
        skillMessage = state.enabled
          ? "\(state.name) enabled for Hermes discovery; JL authorization is unchanged."
          : "\(state.name) disabled."
        await refreshSkills()
      } catch {
        skillMessage = display(error)
      }
      isSkillWorking = false
    }
  }

  func refreshVoice() async {
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    do {
      let status = try await Task.detached {
        try client.voiceStatus(callerID: callerID, sessionID: sessionID)
      }.value
      voiceStatus = status
      if wakePhraseDraft.isEmpty { wakePhraseDraft = status.wake.phrase ?? "hey j l" }
      voiceMessage = Self.voiceSummary(status)
      if status.ownedByCurrentSession {
        voiceEvents = try await Task.detached {
          try client.voiceEvents(callerID: callerID, sessionID: sessionID)
        }.value
        if let passed = voiceEvents.last(where: {
          $0.kind == "wake_phrase_test" && $0.status == "passed"
        })?.text {
          testedWakePhrase = passed
          wakePhraseMessage = "Test passed. This phrase can now become the default."
        }
      } else {
        voiceEvents = []
      }
    } catch {
      voiceMessage = display(error)
    }
  }

  func refreshAutomation() async {
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    do {
      let status = try await Task.detached {
        try client.automationStatus(callerID: callerID, sessionID: sessionID)
      }.value
      let schedules = try await Task.detached {
        try client.automationSchedules(callerID: callerID, sessionID: sessionID)
      }.value
      let history = try await Task.detached {
        try client.automationHistory(callerID: callerID, sessionID: sessionID)
      }.value
      automationStatus = status
      self.schedules = schedules
      scheduleHistory = history
      automationMessage = Self.automationSummary(status)
    } catch {
      automationMessage = display(error)
    }
  }

  func createReminder() {
    guard !isWorking else { return }
    let schedule = reminderRecurring
      ? "every \(reminderScheduleText)"
      : "in \(reminderScheduleText)"
    let name = reminderName
    let note = reminderNote
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    isWorking = true
    Task {
      do {
        _ = try await Task.detached {
          try client.createAutomationReminder(
            name: name,
            schedule: schedule,
            note: note,
            callerID: callerID,
            sessionID: sessionID
          )
        }.value
        automationMessage = "Reminder created paused. Activate it with exact confirmation."
        isWorking = false
        await refreshAutomation()
      } catch {
        automationMessage = display(error)
        isWorking = false
      }
    }
  }

  func activateReminder(_ schedule: AutomationSchedule) {
    guard !isWorking else { return }
    let requestID = UUID().uuidString.lowercased()
    let jobID = schedule.id
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    isWorking = true
    Task {
      do {
        let challenge = try await Task.detached {
          try client.requestAutomationActivation(
            jobID: jobID,
            requestID: requestID,
            callerID: callerID,
            sessionID: sessionID
          )
        }.value
        pendingAutomationConsentDeadline = ConsentExpiryPolicy.deadline(
          receivedAt: consentClock.now,
          expiresInSeconds: challenge.expiresInSeconds
        )
        pendingAutomationConsent = challenge
        automationMessage = "Exact activation confirmation is required."
      } catch {
        automationMessage = display(error)
      }
      isWorking = false
    }
  }

  func approveAutomation(_ challenge: ConsentChallenge) {
    decideAutomation(challenge, decision: .approve)
  }

  func rejectAutomation(_ challenge: ConsentChallenge) {
    decideAutomation(challenge, decision: .reject)
  }

  func pauseReminder(_ schedule: AutomationSchedule) {
    guard !isWorking else { return }
    let jobID = schedule.id
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    isWorking = true
    Task {
      do {
        _ = try await Task.detached {
          try client.pauseAutomationReminder(
            jobID: jobID,
            callerID: callerID,
            sessionID: sessionID
          )
        }.value
        automationMessage = "Reminder paused and authorization revoked."
        isWorking = false
        await refreshAutomation()
      } catch {
        automationMessage = display(error)
        isWorking = false
      }
    }
  }

  func removeReminder(_ schedule: AutomationSchedule) {
    guard !isWorking else { return }
    let jobID = schedule.id
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    isWorking = true
    Task {
      do {
        _ = try await Task.detached {
          try client.removeAutomationReminder(
            jobID: jobID,
            callerID: callerID,
            sessionID: sessionID
          )
        }.value
        automationMessage = "Reminder removed."
        isWorking = false
        await refreshAutomation()
      } catch {
        automationMessage = display(error)
        isWorking = false
      }
    }
  }

  func stopAllAutomation() {
    guard !isWorking else { return }
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    isWorking = true
    Task {
      do {
        automationStatus = try await Task.detached {
          try client.stopAllAutomation(
            callerID: callerID,
            sessionID: sessionID
          )
        }.value
        automationMessage = "All schedules stopped. Scheduler remains stopped."
        isWorking = false
        await refreshAutomation()
      } catch {
        automationMessage = display(error)
        isWorking = false
      }
    }
  }

  func startVoice() { updateVoice(.startVoice) }
  func stopVoice() { updateVoice(.stopVoice) }
  func startWake() { updateVoice(.startWake) }
  func stopWake() { updateVoice(.stopWake) }

  func testWakePhrase() {
    let phrase = normalizedWakePhrase
    guard !isWorking else { return }
    isWorking = true
    Task {
      do {
        voiceStatus = try await Task.detached {
          try self.client.testWakePhrase(
            phrase, callerID: self.callerID, sessionID: self.sessionID
          )
        }.value
        testedWakePhrase = nil
        wakePhraseMessage = "Listening for ‘\(phrase)’. Say it, then refresh the test result."
      } catch {
        wakePhraseMessage = display(error)
      }
      isWorking = false
    }
  }

  func saveWakePhrase() {
    let phrase = normalizedWakePhrase
    guard testedWakePhrase == phrase, !isWorking else { return }
    isWorking = true
    Task {
      do {
        voiceStatus = try await Task.detached {
          try self.client.setWakePhrase(
            phrase, callerID: self.callerID, sessionID: self.sessionID
          )
        }.value
        wakePhraseMessage = "Default wake phrase saved."
      } catch {
        wakePhraseMessage = display(error)
      }
      isWorking = false
    }
  }

  var normalizedWakePhrase: String {
    wakePhraseDraft.split(whereSeparator: { $0.isWhitespace })
      .joined(separator: " ").lowercased()
  }

  var canSaveWakePhrase: Bool {
    testedWakePhrase == normalizedWakePhrase && !isWorking
  }

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

  private func decideAssistant(
    _ challenge: ConsentChallenge,
    decision consentDecision: ConsentDecision
  ) {
    guard !isWorking else { return }
    guard let deadline = pendingAssistantConsentDeadline,
      !ConsentExpiryPolicy.isExpired(deadline: deadline, now: consentClock.now)
    else {
      pendingAssistantConsent = nil
      pendingAssistantConsentDeadline = nil
      assistantState = "consent_expired"
      assistantResultText = "Consent expired. Send the request again."
      return
    }
    pendingAssistantConsent = nil
    pendingAssistantConsentDeadline = nil
    isWorking = true
    let signer = signer
    let client = client
    Task {
      do {
        let response = try await Task.detached {
          let signature = try signer.sign(
            challenge: challenge,
            decision: consentDecision
          )
          return try AssistantResponse(
            result: client.submitConsentResult(
              challenge: challenge,
              decision: consentDecision,
              signature: signature
            )
          )
        }.value
        assistantState = response.state
        assistantResultText = response.resultJSON
        isWorking = false
      } catch {
        handleAssistantError(error)
      }
    }
  }

  private func decideAutomation(
    _ challenge: ConsentChallenge,
    decision consentDecision: ConsentDecision
  ) {
    guard !isWorking else { return }
    guard let deadline = pendingAutomationConsentDeadline,
      !ConsentExpiryPolicy.isExpired(deadline: deadline, now: consentClock.now)
    else {
      pendingAutomationConsent = nil
      pendingAutomationConsentDeadline = nil
      automationMessage = "consent_expired: request activation again."
      return
    }
    pendingAutomationConsent = nil
    pendingAutomationConsentDeadline = nil
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
          return try client.submitAutomationConsent(
            challenge: challenge,
            decision: consentDecision,
            signature: signature
          )
        }.value
        automationMessage =
          consentDecision == .reject
          ? "Activation rejected. Reminder stayed paused."
          : "Activation \(state)."
        isWorking = false
        await refreshAutomation()
      } catch {
        automationMessage = display(error)
        isWorking = false
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
    automationStatus = status.automation
    consentIdentityMatches =
      status.consentEnrollmentCurrent
      && status.consentKeyFingerprint == localConsentFingerprint
    runtimeMessage = "JL runtime ready (PID \(status.runtimePID))."
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
    runtimeMessage = display(error)
    if !RuntimeStatusSnapshotPolicy.shouldRetain(after: error) {
      runtimePID = nil
      computerUseStatus = nil
      automationStatus = nil
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

  private func handleAssistantError(_ error: Error) {
    assistantState = assistantErrorState(error)
    assistantResultText = display(error)
    isWorking = false
    apply(error)
  }

  private func display(_ error: Error) -> String {
    (error as? LocalizedError)?.errorDescription ?? "Request failed safely."
  }

  private func assistantErrorState(_ error: Error) -> String {
    if case RuntimeClientError.server(let code, _) = error {
      return code
    }
    if case RuntimeClientError.malformedResponse = error {
      return "malformed_response"
    }
    return "request_failed"
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

  private static func automationSummary(_ status: AutomationStatus) -> String {
    if status.stopped {
      return "Scheduler is stopped. Use Stop All only for shutdown; restart runtime to clear it."
    }
    if !status.available {
      return "Scheduler storage is unavailable."
    }
    if !status.schedulerEnabled {
      return "Scheduler service is disabled. Schedules can be managed but will not run."
    }
    return "Scheduler service is enabled for fixed local no-agent reminders."
  }
}
