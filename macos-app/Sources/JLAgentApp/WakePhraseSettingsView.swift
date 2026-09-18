import SwiftUI

struct WakePhraseSettingsView: View {
  @ObservedObject var viewModel: AgentViewModel
  @State private var language = "auto"
  @State private var sensitivity = 600.0
  @State private var silenceDuration = 1.0
  @State private var followUpTimeout = 3.0

  private var microphoneLevel: Double {
    min(max(Double(viewModel.microphoneTestStatus.level) / 32767.0, 0), 1)
  }

  var body: some View {
    Form {
      Section("Voice") {
        Text("Voice dùng Hermes native 0.21.2. Microphone chỉ mở sau khi bấm cat và đóng khi session dừng hoặc hết inactivity.")
          .font(.caption)
          .foregroundStyle(.secondary)
        LabeledContent("Voice system", value: "Hermes Voice")
        LabeledContent("Wake listener", value: "Disabled")
        Picker("Language", selection: $language) {
          Text("Auto").tag("auto")
          Text("Vietnamese").tag("vi")
          Text("English").tag("en")
        }
        VStack(alignment: .leading, spacing: 4) {
          HStack {
            Text("Mic sensitivity")
            Spacer()
            Text("\(Int(sensitivity))")
              .monospacedDigit()
          }
          Slider(value: $sensitivity, in: 200...2000, step: 50)
          Text("Ngưỡng thấp hơn sẽ nhạy hơn với microphone.")
            .font(.caption2)
            .foregroundStyle(.secondary)
        }
        VStack(alignment: .leading, spacing: 4) {
          HStack {
            Text("End-of-speech delay")
            Spacer()
            Text("\(silenceDuration, specifier: "%.2f") s")
              .monospacedDigit()
          }
          Slider(value: $silenceDuration, in: 0.5...5.0, step: 0.05)
        }
        VStack(alignment: .leading, spacing: 4) {
          HStack {
            Text("Follow-up timeout")
            Spacer()
            Text("\(followUpTimeout, specifier: "%.1f") s")
              .monospacedDigit()
          }
          Slider(value: $followUpTimeout, in: 0.5...60.0, step: 0.5)
        }
        HStack {
          Button("Lưu Voice settings") {
            viewModel.saveVoiceSettings(
              language: language,
              silenceThreshold: Int(sensitivity),
              silenceDuration: silenceDuration,
              followUpTimeout: followUpTimeout
            )
          }
          .disabled(viewModel.isWorking)
          Button("Test TTS") { viewModel.testVoiceTTS(language: language) }
            .disabled(viewModel.isWorking)
        }
        Text(viewModel.voiceSettingsMessage)
          .font(.caption)
          .foregroundStyle(.secondary)
      }

      Section("Microphone") {
        HStack {
          Button(viewModel.microphoneTestStatus.active ? "Stop microphone test" : "Test microphone") {
            if viewModel.microphoneTestStatus.active {
              viewModel.stopMicrophoneTest()
            } else {
              viewModel.startMicrophoneTest()
            }
          }
          .disabled(viewModel.isWorking)
          Spacer()
          Text(viewModel.microphoneTestStatus.state)
            .foregroundStyle(.secondary)
        }
        ProgressView(value: microphoneLevel)
        LabeledContent(
          "Live level",
          value: "\(viewModel.microphoneTestStatus.level) / 32767"
        )
        Text("Bài test mở microphone độc lập; hãy Stop sau khi kiểm tra để giải phóng thiết bị.")
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
          Button("Refresh Diagnostics") { viewModel.refreshOptionalStatus() }
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

      Section("Nhật ký ứng dụng") {
        Toggle("Hiển thị nhật ký cạnh Companion", isOn: $viewModel.liveLogEnabled)
        Text("Theo dõi runtime, Hermes và Voice. Nhật ký không ghi credential hoặc raw audio.")
          .font(.caption)
          .foregroundStyle(.secondary)
      }
    }
    .onAppear { syncFromViewModel() }
    .task {
      await viewModel.refreshVoiceSettings()
      syncFromViewModel()
    }
    .task(id: viewModel.microphoneTestStatus.active) {
      while !Task.isCancelled, viewModel.microphoneTestStatus.active {
        await viewModel.refreshMicrophoneTest()
        try? await Task.sleep(for: .milliseconds(250))
      }
    }
  }

  private func syncFromViewModel() {
    language = viewModel.voiceSettings.language
    sensitivity = Double(viewModel.voiceSettings.silenceThreshold)
    silenceDuration = viewModel.voiceSettings.silenceDuration
    followUpTimeout = viewModel.voiceSettings.followUpTimeout
  }
}
