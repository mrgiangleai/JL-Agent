import AppKit
import Darwin
import Foundation
import JLAgentCore

struct AppLogEntry: Identifiable, Equatable {
  let id = UUID()
  let timestamp: String
  let message: String
}

enum VoiceDisplayPhase: Equatable {
  case sleeping
  case wake
  case wakeDetected
  case listening
  case transcript(String)
  case thinking
  case speaking
  case acting
  case error(String)
}

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
  @Published var voiceEngineStatus: VoiceEngineStatus?
  @Published var voiceEvents: [VoiceEvent] = []
  @Published var voiceMessage = "Voice is off by default."
  @Published var voiceSettings = VoiceSettings(
    language: "auto", silenceThreshold: 600, silenceDuration: 1.0, followUpTimeout: 3.0
  )
  @Published var microphoneTestStatus = MicrophoneTestStatus(active: false, level: 0, state: "idle")
  @Published var voiceSettingsMessage = "Voice settings have not been loaded."
  @Published var pttTranscript = ""
  @Published private(set) var latestVoiceTranscript = ""
  @Published private(set) var voiceWakeDiagnostic: String?
  @Published private(set) var voiceDisplayPhase: VoiceDisplayPhase = .sleeping
  @Published var automationStatus: AutomationStatus?
  @Published var schedules: [AutomationSchedule] = []
  @Published var scheduleHistory: [AutomationExecution] = []
  @Published var automationMessage = "Scheduler status has not been checked."
  @Published var reminderName = "Local reminder"
  @Published var reminderNote = "JL reminder"
  @Published var reminderScheduleText = "5m"
  @Published var reminderRecurring = false
  @Published var pendingAutomationConsent: ConsentChallenge?
  @Published var runtimePID: Int?
  @Published var hermesRevision = "unknown"
  @Published var runtimeMessage = "Starting the packaged JL runtime…"
  @Published var consentIdentityMatches = false
  @Published var migrationStatus = "Not checked"
  @Published var diagnosticMessage = "Diagnostics have not been exported."
  @Published var isWorking = false
  @Published var companionAnswer: String?
  @Published var liveLogEnabled = UserDefaults.standard.bool(forKey: "JLAgent.liveLogEnabled") {
    didSet {
      guard oldValue != liveLogEnabled else { return }
      UserDefaults.standard.set(liveLogEnabled, forKey: "JLAgent.liveLogEnabled")
      NotificationCenter.default.post(name: .jlLiveLogVisibilityChanged, object: nil)
    }
  }
  @Published private(set) var liveLogEntries: [AppLogEntry] = []

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
  private var voiceOperationInFlight = false
  private var voiceRefreshInFlight = false
  private var voiceEventsInitialized = false
  private var lastVoiceEventSequence = 0
  private var voicePhaseResetTask: Task<Void, Never>?
  private var voiceListeningTimeoutTask: Task<Void, Never>?
  private var voiceRefreshTask: Task<Void, Never>?

  init(paths: RuntimePaths = RuntimePaths()) {
    self.paths = paths
    let credentials = KeychainCredentialProvider()
    let signer = ConsentSigningKey()
    self.credentials = credentials
    self.signer = signer
    self.client = JLRuntimeClient(paths: paths, credentials: credentials)
    self.runtimeProcess = RuntimeProcessController(paths: paths)
    appendLog("JL Agent đã khởi tạo")
  }

  func initialize() {
    guard !initialized else { return }
    initialized = true
    connectionState = .connecting
    appendLog("Đang khởi động packaged runtime")
    let paths = paths
    let credentials = credentials
    let signer = signer
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    Task {
      do {
        try await runtimeProcess.ensureReady()
        appendLog("Runtime đã sẵn sàng; đang tải trạng thái")
        let observed = try await Task.detached {
          _ = try signer.provisionPublicKey(at: paths.consentPublicKey)
          _ = try credentials.loadOrImport(from: paths.credential)
          return try signer.publicKeyFingerprint()
        }.value
        appendLog("Trust đã sẵn sàng; đang tải voice")
        await refreshVoice()
        await refreshVoiceSettings()
        startVoiceRefreshLoop()
        let status = try await Task.detached {
          try client.status(callerID: callerID, sessionID: sessionID)
        }.value
        apply(status, localConsentFingerprint: observed)
        appendLog("Đã tải trạng thái runtime và kiểm tra trust")
        refreshDiagnostics()
      } catch {
        appendLog("Runtime khởi động lỗi: \(display(error))")
        apply(error)
      }
    }
  }

  func refreshStatus(includeOptional: Bool = false) {
    connectionState = .connecting
    appendLog(includeOptional ? "Đang tải runtime và optional status" : "Đang tải runtime status")
    let client = client
    let signer = signer
    let callerID = callerID
    let sessionID = sessionID
    Task {
      do {
        try await runtimeProcess.ensureReady()
        let observed = try await Task.detached {
          (
            try client.status(
              callerID: callerID,
              sessionID: sessionID,
              includeOptional: includeOptional
            ),
            try signer.publicKeyFingerprint()
          )
        }.value
        apply(observed.0, localConsentFingerprint: observed.1)
        appendLog(includeOptional ? "Đã tải optional status" : "Đã tải runtime status")
      } catch {
        appendLog("Tải runtime status lỗi: \(display(error))")
        apply(error)
      }
    }
  }

  func refreshOptionalStatus() {
    refreshDiagnostics()
    refreshStatus(includeOptional: true)
  }

  func restartRuntime() {
    guard !isWorking else { return }
    isWorking = true
    runtimeMessage = "Restarting the packaged JL runtime…"
    appendLog("Đang restart packaged runtime")
    Task {
      do {
        try await runtimeProcess.restart()
        isWorking = false
        runtimeMessage = "JL runtime restarted safely."
        appendLog("Packaged runtime đã restart")
        refreshStatus()
      } catch {
        isWorking = false
        appendLog("Restart runtime lỗi: \(display(error))")
        apply(error)
      }
    }
  }

  func stopRuntime() {
    guard !isWorking else { return }
    let stopped = runtimeProcess.stopOwnedRuntime()
    appendLog(stopped ? "Đã stop packaged runtime" : "Runtime không thuộc app; giữ nguyên process")
    runtimePID = nil
    connectionState = .unavailable
    runtimeMessage = stopped
      ? "JL runtime stopped. Use Refresh Status to start it again."
      : "JL runtime is not owned by this app and was left running."
  }

  func stopRuntimeForTermination() {
    _ = runtimeProcess.stopOwnedRuntime()
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
    companionAnswer = nil
    assistantState = "sending"
    assistantResultText = "Sending typed assistant request..."
    appendLog("Đang gửi typed request tới Hermes")
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
        } else {
          companionAnswer = Self.companionReply(from: response.resultJSON)
        }
        isWorking = false
        appendLog("Hermes đã trả lời typed request")
      } catch {
        appendLog("Typed request lỗi: \(display(error))")
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
    companionAnswer = nil
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

  func refreshDiagnostics() {
    let marker = paths.root.appendingPathComponent("storage-migration.json")
    guard let data = try? Data(contentsOf: marker),
      let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
      value["schema_version"] as? Int == 1,
      let state = value["state"] as? String
    else {
      migrationStatus = "Unavailable or invalid"
      return
    }
    migrationStatus = state
  }

  func exportDiagnostics() {
    let diagnosticURL = paths.logs.appendingPathComponent("jl-agent-diagnostics.json")
    let computerUse = computerUseStatus
    let voice = voiceStatus
    let payload: [String: Any] = [
      "schema_version": 1,
      "runtime_state": connectionState.rawValue,
      "runtime_pid": runtimePID.map { $0 } ?? NSNull(),
      "transport": "AF_UNIX",
      "hermes_revision": hermesRevision,
      "voice_host_bundle_id": "com.jlagent.voice-runtime",
      "voice_enabled": voice.map { $0.enabled } ?? NSNull(),
      "voice_activation_approved": voice.map { $0.activationApproved } ?? NSNull(),
      "cua_driver_bundle_id": computerUse?.driverBundleID ?? NSNull(),
      "cua_driver_team_id": computerUse?.driverTeamID ?? NSNull(),
      "cua_driver_identity_ready": computerUse?.driverIdentityReady ?? NSNull(),
      "cua_driver_execution_ready": computerUse?.executionReady ?? NSNull(),
      "migration_status": migrationStatus,
      "locations": [
        "runtime": paths.root.path,
        "hermes": paths.hermes.path,
        "models": paths.modelCache.path,
        "logs": paths.logs.path,
      ],
    ]
    do {
      try FileManager.default.createDirectory(
        at: paths.logs,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
      )
      let data = try JSONSerialization.data(
        withJSONObject: payload,
        options: [.prettyPrinted, .sortedKeys]
      )
      try data.write(to: diagnosticURL, options: .atomic)
      chmod(diagnosticURL.path, 0o600)
      diagnosticMessage = "Redacted diagnostics exported to \(diagnosticURL.path)."
    } catch {
      diagnosticMessage = "Diagnostics export failed: \(error.localizedDescription)"
    }
  }

  func repairModelCache() {
    guard !isWorking else { return }
    isWorking = true
    defer { isWorking = false }
    do {
      if FileManager.default.fileExists(atPath: paths.modelCache.path) {
        let values = try paths.modelCache.resourceValues(
          forKeys: [.isDirectoryKey, .isSymbolicLinkKey]
        )
        guard values.isDirectory == true, values.isSymbolicLink != true else {
          throw RuntimeClientError.invalidRequest("model cache is not a private directory")
        }
        let backup = paths.modelCache.deletingLastPathComponent()
          .appendingPathComponent("models.repair-\(UUID().uuidString)")
        try FileManager.default.moveItem(at: paths.modelCache, to: backup)
      }
      try FileManager.default.createDirectory(
        at: paths.modelCache,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
      )
      chmod(paths.modelCache.path, 0o700)
      diagnosticMessage = "Model cache repaired; previous cache was moved aside for recovery."
    } catch {
      diagnosticMessage = "Model cache repair failed: \(error.localizedDescription)"
    }
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
    guard !voiceRefreshInFlight else { return }
    voiceRefreshInFlight = true
    defer { voiceRefreshInFlight = false }
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    do {
      let status = try await Task.detached {
        try client.voiceStatus(callerID: callerID, sessionID: sessionID)
      }.value
      voiceStatus = status
      voiceEngineStatus = try? await Task.detached {
        try client.voiceEngineStatus(callerID: callerID, sessionID: sessionID)
      }.value
      voiceMessage = Self.voiceSummary(status)
      if status.ownedByCurrentSession {
        voiceEvents = try await Task.detached {
          try client.voiceEvents(callerID: callerID, sessionID: sessionID)
        }.value
        observeVoiceEvents()
        if let reply = voiceEvents.last(where: { $0.kind == "reply" })?.text,
          !reply.isEmpty
        {
          companionAnswer = reply
        }
        if let transcript = voiceEvents.last(where: { $0.kind == "ptt_transcript" })?.text,
          !transcript.isEmpty
        {
          pttTranscript = transcript
        }
      } else {
        voiceEvents = []
        voiceEventsInitialized = false
        latestVoiceTranscript = ""
        voiceWakeDiagnostic = nil
        voiceDisplayPhase = .sleeping
      }
    } catch {
      let message = display(error)
      if voiceMessage != message { appendLog("Tải voice status lỗi: \(message)") }
      voiceMessage = message
    }
  }

  func refreshVoiceSettings() async {
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    do {
      voiceSettings = try await Task.detached {
        try client.voiceSettings(callerID: callerID, sessionID: sessionID)
      }.value
      voiceSettingsMessage = "Voice settings đã tải từ Hermes."
    } catch {
      voiceSettingsMessage = display(error)
    }
  }

  func saveVoiceSettings(
    language: String,
    silenceThreshold: Int,
    silenceDuration: Double,
    followUpTimeout: Double
  ) {
    guard !isWorking else { return }
    isWorking = true
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    Task {
      do {
        voiceSettings = try await Task.detached {
          try client.setVoiceSettings(
            language: language,
            silenceThreshold: silenceThreshold,
            silenceDuration: silenceDuration,
            followUpTimeout: followUpTimeout,
            callerID: callerID,
            sessionID: sessionID
          )
        }.value
        voiceSettingsMessage = "Đã lưu Voice settings cho Hermes."
      } catch {
        voiceSettingsMessage = display(error)
      }
      isWorking = false
    }
  }

  func startMicrophoneTest() {
    guard !isWorking else { return }
    isWorking = true
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    Task {
      do {
        microphoneTestStatus = try await Task.detached {
          try client.startMicrophoneTest(callerID: callerID, sessionID: sessionID)
        }.value
      } catch {
        voiceSettingsMessage = display(error)
      }
      isWorking = false
    }
  }

  func refreshMicrophoneTest() async {
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    do {
      microphoneTestStatus = try await Task.detached {
        try client.microphoneTestStatus(callerID: callerID, sessionID: sessionID)
      }.value
    } catch {
      voiceSettingsMessage = display(error)
    }
  }

  func stopMicrophoneTest() {
    guard !isWorking else { return }
    isWorking = true
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    Task {
      do {
        microphoneTestStatus = try await Task.detached {
          try client.stopMicrophoneTest(callerID: callerID, sessionID: sessionID)
        }.value
      } catch {
        voiceSettingsMessage = display(error)
      }
      isWorking = false
    }
  }

  func testVoiceTTS(language: String) {
    guard !isWorking else { return }
    isWorking = true
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    Task {
      do {
        try await Task.detached {
          try client.testVoiceTTS(language: language, callerID: callerID, sessionID: sessionID)
        }.value
        voiceSettingsMessage = "Đã phát thử TTS bằng Hermes."
      } catch {
        voiceSettingsMessage = display(error)
      }
      isWorking = false
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
  func toggleVoiceSession() {
    if voiceStatus?.ownedByCurrentSession == true,
      voiceStatus?.voice.active == true
    {
      stopVoice()
    } else {
      startVoice()
    }
  }

  func setVoiceEngine(_ engine: String) {
    guard !isWorking, voiceEngineStatus?.selected != engine else { return }
    isWorking = true
    appendLog("Đang chọn voice engine: \(engine)")
    let client = client
    let callerID = callerID
    let sessionID = sessionID
    Task {
      do {
        voiceEngineStatus = try await Task.detached {
          return try client.setVoiceEngine(engine, callerID: callerID, sessionID: sessionID)
        }.value
        appendLog("Đã chọn voice engine: \(engine)")
      } catch {
        appendLog("Chọn voice engine lỗi: \(display(error))")
      }
      isWorking = false
      await refreshVoice()
    }
  }

  func dismissCompanionAnswer() {
    companionAnswer = nil
  }

  private enum VoiceOperation: Sendable {
    case startVoice, stopVoice

    var logLabel: String {
      switch self {
      case .startVoice: "start voice"
      case .stopVoice: "stop voice"
      }
    }
  }

  private func updateVoice(_ operation: VoiceOperation) {
    guard !isWorking, !voiceOperationInFlight else { return }
    voiceOperationInFlight = true
    isWorking = true
    if case .startVoice = operation { companionAnswer = nil }
    appendLog("Đang thực hiện voice operation: \(operation.logLabel)")
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
          }
        }.value
        voiceStatus = status
        voiceMessage = Self.voiceSummary(status)
        switch operation {
        case .startVoice:
          voiceDisplayPhase = .listening
        case .stopVoice:
          voiceDisplayPhase = .sleeping
        }
        voiceOperationInFlight = false
        isWorking = false
        appendLog("Voice operation hoàn tất: \(operation.logLabel)")
        await refreshVoice()
      } catch {
        appendLog("Voice operation lỗi: \(display(error))")
        voiceMessage = display(error)
        voiceDisplayPhase = .error(display(error))
        voiceStatus = nil
        voiceOperationInFlight = false
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
      companionAnswer = result.output
      isWorking = false
      await refreshActivity()
    } catch {
      handleRequestError(error)
    }
  }

  private func apply(_ status: RuntimeStatus, localConsentFingerprint: String) {
    runtimePID = status.runtimePID
    hermesRevision = status.hermesRevision
    if status.optionalChecksLoaded {
      computerUseStatus = status.computerUse
      automationStatus = status.automation
    } else {
      computerUseStatus = nil
      automationStatus = nil
    }
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

  private func appendLog(_ message: String) {
    let formatter = DateFormatter()
    formatter.dateFormat = "HH:mm:ss"
    liveLogEntries.append(
      AppLogEntry(timestamp: formatter.string(from: Date()), message: message)
    )
    if liveLogEntries.count > 80 {
      liveLogEntries.removeFirst(liveLogEntries.count - 80)
    }
  }

  private func observeVoiceEvents() {
    guard let latestSequence = voiceEvents.map(\.sequence).max() else { return }
    if !voiceEventsInitialized {
      voiceEventsInitialized = true
      lastVoiceEventSequence = latestSequence
      applyLatestVoiceStatusIfNeeded()
      return
    }
    let newEvents = voiceEvents
      .filter { $0.sequence > lastVoiceEventSequence }
      .sorted { $0.sequence < $1.sequence }
    lastVoiceEventSequence = latestSequence
    for event in newEvents {
      switch event.kind {
      case "wake_detected":
        voiceWakeDiagnostic = nil
        voiceDisplayPhase = .wakeDetected
        scheduleVoiceListeningTimeout()
      case "transcript":
        voiceWakeDiagnostic = nil
        voiceListeningTimeoutTask?.cancel()
        latestVoiceTranscript = event.text ?? ""
        voiceDisplayPhase = .transcript(event.text ?? "Đã nhận lời nói")
      case "partial_transcript":
        voiceWakeDiagnostic = nil
        latestVoiceTranscript = event.text ?? ""
        voiceDisplayPhase = .transcript(event.text ?? "Đang nghe…")
      case "stt_transcript":
        voiceWakeDiagnostic = nil
        voiceListeningTimeoutTask?.cancel()
        latestVoiceTranscript = event.text ?? ""
        // Keep the complete final transcript visible until the processing
        // status arrives; no artificial UI delay hides what JL heard.
        voiceDisplayPhase = .transcript(event.text ?? "Đã nhận lời nói")
      case "ptt_transcript":
        voiceDisplayPhase = .transcript(event.text ?? "Đã nhận lời nói")
      case "reply":
        voiceWakeDiagnostic = nil
        voiceListeningTimeoutTask?.cancel()
        voiceDisplayPhase = .speaking
      case "tts_playback_complete":
        voiceWakeDiagnostic = "Đã phát xong · đang chờ bạn nói tiếp"
        voiceListeningTimeoutTask?.cancel()
        voiceDisplayPhase = .listening
      case "voice_status":
        if let status = event.status { applyVoiceStatus(status) }
      case "voice_error":
        voiceListeningTimeoutTask?.cancel()
        voiceDisplayPhase = .error(event.code ?? "voice_error")
      default:
        break
      }
    }
  }

  private func applyLatestVoiceStatusIfNeeded() {
    guard let status = voiceEvents.reversed().first(where: {
      $0.kind == "voice_status" && $0.status != nil
    })?.status else { return }
    applyVoiceStatus(status)
  }

  private func applyVoiceStatus(_ status: String) {
    switch status {
    case "pipeline_starting":
      voiceWakeDiagnostic = "Voice đang khởi động…"
      voiceDisplayPhase = .sleeping
    case "pipeline_ready":
      voiceWakeDiagnostic = "Bấm cat để mở mic · Hermes Voice đang chờ lời nói"
    case "thinking":
      voiceWakeDiagnostic = nil
      voiceListeningTimeoutTask?.cancel()
      voiceDisplayPhase = .thinking
    case "processing":
      voiceWakeDiagnostic = nil
      voiceListeningTimeoutTask?.cancel()
      voiceDisplayPhase = .thinking
    case "stt_rejected":
      voiceListeningTimeoutTask?.cancel()
      voiceDisplayPhase = .error("Không chắc chắn nội dung nghe được · hãy thử lại")
    case "tts_started":
      voiceWakeDiagnostic = nil
      voiceListeningTimeoutTask?.cancel()
      voiceDisplayPhase = .speaking
    case "tts_provider:local", "tts_provider:gemini":
      break
    case "command_listening", "listening":
      voiceWakeDiagnostic = nil
      voiceDisplayPhase = .listening
    case "conversational_listening":
      voiceWakeDiagnostic = "Đang chờ câu tiếp theo"
      voiceListeningTimeoutTask?.cancel()
      voiceDisplayPhase = .listening
    case "wake_waiting":
      voiceWakeDiagnostic = nil
      voiceListeningTimeoutTask?.cancel()
      voiceDisplayPhase = .sleeping
    default:
      break
    }
  }

  private func startVoiceRefreshLoop() {
    guard voiceRefreshTask == nil else { return }
    voiceRefreshTask = Task { @MainActor [weak self] in
      while !Task.isCancelled {
        try? await Task.sleep(for: .milliseconds(200))
        guard let self, !Task.isCancelled else { return }
        guard self.voiceStatus?.voice.active == true
          || self.voiceDisplayPhase != .sleeping
        else { continue }
        await self.refreshVoice()
      }
    }
  }

  private func scheduleVoicePhase(_ phase: VoiceDisplayPhase, after duration: Duration) {
    voicePhaseResetTask?.cancel()
    voicePhaseResetTask = Task { @MainActor [weak self] in
      try? await Task.sleep(for: duration)
      guard !Task.isCancelled, let self else { return }
      self.voiceDisplayPhase = phase
    }
  }

  private func scheduleVoiceListeningTimeout() {
    voiceListeningTimeoutTask?.cancel()
    voiceListeningTimeoutTask = Task { @MainActor [weak self] in
      try? await Task.sleep(for: .seconds(22))
      guard !Task.isCancelled, let self else { return }
      guard self.voiceDisplayPhase == .wakeDetected || self.voiceDisplayPhase == .listening else {
        return
      }
      self.voiceDisplayPhase = .error("Không nhận được câu lệnh · hãy thử lại")
    }
  }

  private static func companionReply(from resultJSON: String) -> String? {
    guard let data = resultJSON.data(using: .utf8),
      let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else { return nil }
    for key in ["reply", "output", "message"] {
      if let value = object[key] as? String, !value.isEmpty { return value }
    }
    return nil
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

  var runtimeLocation: String { paths.root.path }
  var hermesLocation: String { paths.hermes.path }
  var modelCacheLocation: String { paths.modelCache.path }
  var logsLocation: String { paths.logs.path }

  private static func voiceSummary(_ status: VoiceStatus) -> String {
    if !status.enabled {
      return "Voice is disabled in the foreground runtime."
    }
    if !status.activationApproved {
      return "Voice activation awaits explicit dependency/model and Microphone approval."
    }
    if !status.voice.available {
      return status.voice.details ?? "Microphone or speech-to-text is unavailable."
    }
    if status.voice.active { return "Hermes Voice session đang mở; microphone đang hoạt động." }
    return "Voice session đang ngủ; microphone đã đóng."
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
