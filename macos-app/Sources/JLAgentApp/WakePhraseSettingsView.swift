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
    }
  }
}
