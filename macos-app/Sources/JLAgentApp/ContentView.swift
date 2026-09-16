import JLAgentCore
import SwiftUI

struct ContentView: View {
  @ObservedObject var viewModel: AgentViewModel

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      HStack {
        Circle()
          .fill(statusColor)
          .frame(width: 10, height: 10)
        Text("JL Agent: \(viewModel.connectionState.rawValue)")
          .font(.headline)
        Spacer()
        Button("Refresh Status") { viewModel.refreshStatus() }
        Button("Refresh Credential") { viewModel.refreshCredential() }
        Button("Rotate Consent Key") { viewModel.rotateConsentKey() }
      }

      Text("Session: \(viewModel.sessionID)")
        .font(.caption.monospaced())
        .foregroundStyle(.secondary)

      GroupBox("Request / action") {
        Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 8) {
          GridRow {
            Text("Capability")
            TextField("Capability", text: $viewModel.draft.capabilityID)
          }
          GridRow {
            Text("Action")
            TextField("Action", text: $viewModel.draft.action)
          }
          GridRow {
            Text("Permissions")
            TextField(
              "Comma-separated permission scopes",
              text: $viewModel.draft.requestedPermission
            )
          }
          GridRow {
            Text("Target")
            TextField("Resolved target", text: $viewModel.draft.resolvedTarget)
          }
          GridRow {
            Text("Foreground")
            TextField("Observed foreground app", text: $viewModel.draft.foregroundApp)
          }
        }
        Text("Arguments (JSON object)")
          .font(.caption)
          .foregroundStyle(.secondary)
        TextEditor(text: $viewModel.draft.argumentsJSON)
          .font(.body.monospaced())
          .frame(minHeight: 70, maxHeight: 100)
          .overlay(RoundedRectangle(cornerRadius: 4).stroke(.quaternary))
        HStack {
          Toggle("Target is within workspace", isOn: $viewModel.draft.targetWithinWorkspace)
          Toggle("Reversible", isOn: $viewModel.draft.reversible)
          Spacer()
          Button("Send / Prepare") { viewModel.sendRequest() }
            .keyboardShortcut(.return, modifiers: [.command])
            .disabled(viewModel.isWorking)
        }
      }

      GroupBox("Decision and result") {
        HStack(alignment: .top) {
          Text(viewModel.decision)
            .font(.headline)
            .frame(width: 90, alignment: .leading)
          ScrollView {
            Text(viewModel.resultText)
              .textSelection(.enabled)
              .frame(maxWidth: .infinity, alignment: .leading)
          }
          .frame(minHeight: 70, maxHeight: 110)
        }
      }

      GroupBox("macOS Permissions") {
        if let status = viewModel.computerUseStatus {
          VStack(alignment: .leading, spacing: 8) {
            HStack {
              Text("Computer Use")
                .font(.headline)
              Spacer()
              Text(status.executionReady ? "ready" : status.health)
                .foregroundStyle(status.executionReady ? .green : .orange)
              Button("Recheck") { viewModel.refreshStatus() }
            }
            Text("Foreground runtime PID: \(viewModel.runtimePIDText)")
              .font(.caption.monospaced())
              .foregroundStyle(.secondary)
            Text(driverSummary(status))
              .font(.caption)
              .foregroundStyle(.secondary)
            Text(status.blockedReason)
              .font(.caption)
              .foregroundStyle(status.executionReady ? .green : .orange)
            ForEach(status.permissions) { permission in
              HStack(alignment: .top, spacing: 10) {
                Text(permissionLabel(permission.kind))
                  .frame(width: 130, alignment: .leading)
                Text(permission.state)
                  .frame(width: 110, alignment: .leading)
                Text(permission.explanation)
                  .font(.caption)
                  .foregroundStyle(.secondary)
                Link("Open Settings", destination: permission.settingsURL)
              }
            }
            Text(
              "Permissions belong to CuaDriver (com.trycua.driver). JL never changes TCC or "
                + "prompts repeatedly. After granting, relaunch CuaDriver/runtime if status "
                + "says restartRequired."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
          }
        } else {
          Text("Refresh runtime status to inspect CuaDriver permissions.")
            .font(.caption)
            .foregroundStyle(.secondary)
        }
      }

      GroupBox("Voice + Wake Word") {
        VStack(alignment: .leading, spacing: 8) {
          HStack {
            Text(viewModel.voiceMessage)
              .font(.caption)
              .foregroundStyle(.secondary)
            Spacer()
            Button("Refresh") { Task { await viewModel.refreshVoice() } }
          }
          HStack {
            Button("Call JL") { viewModel.startVoice() }
              .disabled(!canStartVoice)
            Button("Stop Voice") { viewModel.stopVoice() }
              .disabled(viewModel.voiceStatus?.voice.active != true || viewModel.isWorking)
            Button("Arm Wake") { viewModel.startWake() }
              .disabled(!canStartWake)
            Button("Stop Wake") { viewModel.stopWake() }
              .disabled(viewModel.voiceStatus?.wake.active != true || viewModel.isWorking)
            Spacer()
            Text("Voice tool execution: disabled")
              .font(.caption.bold())
              .foregroundStyle(.green)
          }
          if !viewModel.voiceEvents.isEmpty {
            ScrollView {
              VStack(alignment: .leading, spacing: 4) {
                ForEach(viewModel.voiceEvents) { event in
                  Text(voiceEventText(event))
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
              }
            }
            .frame(minHeight: 60, maxHeight: 110)
          }
        }
      }

      GroupBox("Schedules") {
        VStack(alignment: .leading, spacing: 8) {
          HStack {
            Text(viewModel.automationMessage)
              .font(.caption)
              .foregroundStyle(automationColor)
            Spacer()
            Button("Refresh") { Task { await viewModel.refreshAutomation() } }
            Button("Stop All", role: .destructive) { viewModel.stopAllAutomation() }
              .disabled(viewModel.isWorking)
          }
          HStack {
            TextField("Reminder name", text: $viewModel.reminderName)
              .frame(minWidth: 160)
            Picker("Kind", selection: $viewModel.reminderRecurring) {
              Text("One-time").tag(false)
              Text("Recurring").tag(true)
            }
            .pickerStyle(.segmented)
            .frame(width: 180)
            TextField("Delay or interval, e.g. 5m", text: $viewModel.reminderScheduleText)
              .frame(width: 170)
            TextField("Note", text: $viewModel.reminderNote)
            Button("Create Paused") { viewModel.createReminder() }
              .disabled(viewModel.isWorking)
          }
          if viewModel.schedules.isEmpty {
            Text("No schedules.")
              .font(.caption)
              .foregroundStyle(.secondary)
          } else {
            List(viewModel.schedules) { schedule in
              VStack(alignment: .leading, spacing: 4) {
                HStack {
                  Text(schedule.name).font(.headline)
                  Text(schedule.state)
                    .font(.caption.bold())
                    .foregroundStyle(schedule.enabled ? .green : .orange)
                  Spacer()
                  Button("Activate") { viewModel.activateReminder(schedule) }
                    .disabled(schedule.enabled || viewModel.isWorking)
                  Button("Pause") { viewModel.pauseReminder(schedule) }
                    .disabled(!schedule.enabled || viewModel.isWorking)
                  Button("Remove", role: .destructive) { viewModel.removeReminder(schedule) }
                    .disabled(viewModel.isWorking)
                }
                Text(schedule.scheduleDisplay)
                  .font(.caption)
                  .foregroundStyle(.secondary)
                Text(scheduleStatus(schedule))
                  .font(.caption.monospaced())
                  .foregroundStyle(.secondary)
              }
            }
            .frame(minHeight: 120, maxHeight: 170)
          }
          if !viewModel.scheduleHistory.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
              Text("Execution history").font(.caption.bold())
              ForEach(viewModel.scheduleHistory.prefix(6)) { item in
                Text(historyText(item))
                  .font(.caption.monospaced())
                  .textSelection(.enabled)
              }
            }
          }
        }
      }

      GroupBox("Recent safe activity") {
        List(viewModel.activity) { item in
          HStack {
            Text(item.timestamp).frame(width: 180, alignment: .leading)
            Text(item.capabilityID).frame(maxWidth: .infinity, alignment: .leading)
            Text(item.actionClasses.joined(separator: ", "))
            Text(item.executionStatus)
          }
          .font(.caption)
        }
        .frame(minHeight: 130)
      }
    }
    .padding(16)
    .sheet(item: $viewModel.pendingConsent) { challenge in
      ConsentSheet(
        challenge: challenge,
        approve: { viewModel.approve(challenge) },
        reject: { viewModel.reject(challenge) }
      )
    }
    .sheet(item: $viewModel.pendingAutomationConsent) { challenge in
      ConsentSheet(
        challenge: challenge,
        approve: { viewModel.approveAutomation(challenge) },
        reject: { viewModel.rejectAutomation(challenge) }
      )
    }
  }

  private var statusColor: Color {
    switch viewModel.connectionState {
    case .ready: .green
    case .connected, .connecting: .yellow
    case .degraded: .orange
    case .authenticationFailed, .unavailable: .red
    }
  }

  private func permissionLabel(_ kind: String) -> String {
    kind == "screenRecording" ? "Screen Recording" : "Accessibility"
  }

  private func driverSummary(_ status: ComputerUseStatus) -> String {
    if !status.driverAvailable {
      return "cua-driver is not installed. Use the reviewed Hermes installer before "
        + "granting permissions."
    }
    let version = status.driverVersion.map { " \($0)" } ?? ""
    let identity =
      status.driverIdentityReady
      ? "signed \(status.driverBundleID ?? "CuaDriver") / \(status.driverTeamID ?? "team")"
      : "CuaDriver.app identity unavailable"
    return "cua-driver\(version), \(identity): \(status.detail)"
  }

  private var canStartVoice: Bool {
    guard let status = viewModel.voiceStatus else { return false }
    return status.enabled && status.activationApproved && status.voice.available
      && !status.voice.active && !viewModel.isWorking
  }

  private var canStartWake: Bool {
    guard let status = viewModel.voiceStatus else { return false }
    return status.enabled && status.activationApproved && status.wake.available
      && !status.wake.active && !viewModel.isWorking
  }

  private var automationColor: Color {
    guard let status = viewModel.automationStatus else { return .secondary }
    if status.stopped { return .red }
    if !status.available || !status.schedulerEnabled { return .orange }
    return .green
  }

  private func voiceEventText(_ event: VoiceEvent) -> String {
    let value = event.text ?? event.status ?? event.code ?? ""
    return "#\(event.sequence) \(event.kind): \(value)"
  }

  private func scheduleStatus(_ schedule: AutomationSchedule) -> String {
    let next = schedule.nextRunAt ?? "no next run"
    let last = schedule.lastStatus ?? "no history"
    return "\(schedule.id) | next: \(next) | last: \(last)"
  }

  private func historyText(_ item: AutomationExecution) -> String {
    let when = item.finishedAt ?? item.startedAt ?? item.claimedAt ?? "unknown time"
    return "\(when) \(item.jobID) \(item.status)"
  }
}

private struct ConsentSheet: View {
  let challenge: ConsentChallenge
  let approve: () -> Void
  let reject: () -> Void
  @Environment(\.dismiss) private var dismiss

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      Text("Confirm exact action").font(.title2.bold())
      LabeledContent("Capability", value: challenge.capabilityID)
      LabeledContent("Action", value: challenge.action)
      LabeledContent("Category", value: challenge.actionClasses.joined(separator: ", "))
      LabeledContent(
        "Target", value: challenge.targetSummary.isEmpty ? "—" : challenge.targetSummary)
      LabeledContent("Risk", value: challenge.riskLevel.uppercased())
      LabeledContent("Caller", value: challenge.callerID)
      LabeledContent("Session", value: challenge.sessionID)
      Text("This approval is exact, short-lived, session-bound, and one-time.")
        .font(.caption)
        .foregroundStyle(.secondary)
      HStack {
        Spacer()
        Button("Reject", role: .cancel) {
          reject()
          dismiss()
        }
        Button("Approve") {
          approve()
          dismiss()
        }
        .keyboardShortcut(.defaultAction)
      }
    }
    .padding(20)
    .frame(width: 520)
    .interactiveDismissDisabled()
  }
}
