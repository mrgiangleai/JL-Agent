import SwiftUI

struct WakePhraseSettingsView: View {
  @ObservedObject var viewModel: AgentViewModel

  var body: some View {
    Form {
      Section("Voice") {
        Text("Default wake phrase: \(viewModel.voiceStatus?.wake.phrase ?? "hey j l")")
        TextField("Wake phrase", text: $viewModel.wakePhraseDraft)
        Text(viewModel.wakePhraseMessage)
          .font(.caption)
          .foregroundStyle(.secondary)
        HStack {
          Button("Test Phrase") { viewModel.testWakePhrase() }
            .disabled(
              viewModel.wakePhraseDraft.trimmingCharacters(in: .whitespaces).count < 2
                || viewModel.isWorking
            )
          Button("Refresh Test") { Task { await viewModel.refreshVoice() } }
            .disabled(viewModel.isWorking)
          Button("Make Default") { viewModel.saveWakePhrase() }
            .disabled(!viewModel.canSaveWakePhrase)
        }
        Text(
          "Call JL remains available in the main window and starts listening without "
            + "wake-word detection."
        )
        .font(.caption)
        .foregroundStyle(.secondary)
      }

      Section("Diagnostics") {
        LabeledContent("Runtime", value: viewModel.connectionState.rawValue)
        LabeledContent("PID", value: viewModel.runtimePIDText)
        LabeledContent("Hermes pin", value: viewModel.hermesRevision)
        LabeledContent("Voice host", value: "com.jlagent.voice-runtime")
        LabeledContent(
          "CuaDriver",
          value: viewModel.computerUseStatus?.driverBundleID ?? "not detected"
        )
        LabeledContent("Migration", value: viewModel.migrationStatus)
        VStack(alignment: .leading, spacing: 2) {
          Text("Runtime state: \(viewModel.runtimeLocation)")
          Text("Hermes state: \(viewModel.hermesLocation)")
          Text("Model cache: \(viewModel.modelCacheLocation)")
          Text("Logs: \(viewModel.logsLocation)")
        }
        .font(.caption2.monospaced())
        .textSelection(.enabled)
        HStack {
          Button("Refresh Diagnostics") { viewModel.refreshDiagnostics() }
          Button("Export Redacted Diagnostics") { viewModel.exportDiagnostics() }
          Button("Repair Model Cache", role: .destructive) {
            viewModel.repairModelCache()
          }
          .disabled(viewModel.isWorking)
        }
        Text(viewModel.diagnosticMessage)
          .font(.caption)
          .foregroundStyle(.secondary)
      }
    }
  }
}
