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
            Text("Permission")
            TextField("Permission scope", text: $viewModel.draft.requestedPermission)
          }
          GridRow {
            Text("Target")
            TextField("Resolved target", text: $viewModel.draft.resolvedTarget)
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
              Text(status.health)
                .foregroundStyle(status.ready ? .green : .orange)
            }
            Text(driverSummary(status))
              .font(.caption)
              .foregroundStyle(.secondary)
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
    return "cua-driver\(version): \(status.detail)"
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
