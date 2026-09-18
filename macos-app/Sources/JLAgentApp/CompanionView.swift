import AppKit
import Foundation
import SwiftUI

private enum CompanionResources {
  static let bundle: Bundle = {
    let installedPath = Bundle.main.bundleURL
      .appendingPathComponent("Contents/Resources/JLAgent_JLAgentApp.bundle")
    return Bundle(path: installedPath.path) ?? .module
  }()
}

enum CompanionState: String, Equatable {
  case idle, wake, listening, transcript, processing, speaking, conversationalListening
  case thinking, working, success, attention, error, sleeping

  var assetName: String {
    switch self {
    case .wake, .listening, .conversationalListening: return "listening"
    case .transcript, .processing: return "thinking"
    case .speaking: return "speaking"
    default: return rawValue
    }
  }

  var accessibilityLabel: String {
    switch self {
    case .idle: "JL đang sẵn sàng"
    case .wake: "JL Voice đang lắng nghe"
    case .listening: "JL đang lắng nghe"
    case .transcript: "JL đã nhận transcript"
    case .processing: "JL đang xử lý"
    case .speaking: "JL đang nói"
    case .conversationalListening: "JL đang chờ bạn nói tiếp"
    case .thinking: "JL đang suy nghĩ"
    case .working: "JL đang thực hiện"
    case .success: "JL đã hoàn tất"
    case .attention: "JL cần bạn xác nhận"
    case .error: "JL gặp sự cố"
    case .sleeping: "JL đang nghỉ"
    }
  }
}

private struct CompanionCharacterImage: View {
  let state: CompanionState

  private var sourceImage: NSImage? {
    guard let url = CompanionResources.bundle.url(
      forResource: state.assetName,
      withExtension: "png"
    ) else { return nil }
    return NSImage(contentsOf: url)
  }

  var body: some View {
    Group {
      if let image = sourceImage {
        Image(nsImage: image)
          .resizable()
          .interpolation(.high)
          .scaledToFit()
          .frame(width: image.size.width * 0.5, height: image.size.height * 0.5)
      } else {
        Image(systemName: "questionmark.circle")
          .padding(28)
      }
    }
    .id(state.assetName)
  }
}

private struct LiveLogPanel: View {
  let entries: [AppLogEntry]

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack {
        Image(systemName: "list.bullet.rectangle")
        Text("Nhật ký JL")
          .font(.caption.weight(.semibold))
        Spacer()
        Text(String(entries.count))
          .font(.caption2.monospacedDigit())
          .foregroundStyle(.secondary)
      }
      ScrollView {
        LazyVStack(alignment: .leading, spacing: 5) {
          ForEach(entries.suffix(40)) { entry in
            HStack(alignment: .top, spacing: 6) {
              Text(entry.timestamp)
                .foregroundStyle(.secondary)
              Text(entry.message)
                .foregroundStyle(.primary.opacity(0.9))
                .fixedSize(horizontal: false, vertical: true)
            }
            .font(.caption2.monospaced())
            .frame(maxWidth: .infinity, alignment: .leading)
          }
        }
      }
    }
    .padding(10)
    .frame(width: 300, height: 300, alignment: .topLeading)
    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    .overlay {
      RoundedRectangle(cornerRadius: 14, style: .continuous)
        .stroke(.white.opacity(0.12), lineWidth: 0.5)
    }
    .shadow(color: .black.opacity(0.16), radius: 12, y: 5)
  }
}

struct CompanionView: View {
  @ObservedObject var agent: AgentViewModel
  let onOpenMain: () -> Void

  @State private var isHovering = false
  @State private var reaction = false
  @State private var bubbleDismissTask: Task<Void, Never>?
  @State private var lastActivityAt = Date()
  @State private var isSleeping = false

  private var state: CompanionState {
    if agent.pendingAssistantConsent != nil || agent.pendingConsent != nil
      || agent.pendingAutomationConsent != nil { return .attention }
    if agent.connectionState == .authenticationFailed || agent.connectionState == .unavailable
      || agent.connectionState == .degraded
    {
      return .error
    }
    switch agent.voiceDisplayPhase {
    case .wake:
      return .wake
    case .wakeDetected:
      return .wake
    case .listening:
      return .listening
    case .transcript:
      return .transcript
    case .thinking, .acting:
      return .processing
    case .speaking:
      return .speaking
    case .error:
      return .error
    case .sleeping:
      break
    }
    if agent.voiceStatus?.voice.active == true { return .listening }
    if agent.isWorking {
      return agent.decision == "ALLOW" || agent.resultText.contains("executing")
        ? .working : .thinking
    }
    if isSleeping { return .sleeping }
    if agent.companionAnswer != nil { return .success }
    return .idle
  }

  private var answerText: String? { agent.companionAnswer }

  private var voiceStateText: String {
    switch agent.voiceDisplayPhase {
    case .wake:
      return "Hermes Voice đang lắng nghe"
    case .wakeDetected:
      return "Đã nhận wake word · Đang nghe"
    case .listening:
      return "Đang lắng nghe"
    case .transcript(let text):
      return "Đã nghe: \(text)"
    case .thinking:
      if !agent.latestVoiceTranscript.isEmpty {
        return "Đang suy nghĩ · Đã nghe: \(agent.latestVoiceTranscript)"
      }
      return "Đang suy nghĩ"
    case .speaking:
      return "Đang trả lời bằng giọng nói"
    case .acting:
      return "Đang thực hiện"
    case .error(let message):
      return "Lỗi voice: \(message)"
    case .sleeping:
      break
    }
    if let diagnostic = agent.voiceWakeDiagnostic { return diagnostic }
    if !agent.pttTranscript.isEmpty { return "Transcript: \(agent.pttTranscript)" }
    if let status = agent.voiceStatus, !status.voice.available {
      return "Lỗi voice: \(status.voice.details ?? "Microphone hoặc speech-to-text không khả dụng.")"
    }
    if agent.voiceStatus == nil { return "Voice đang khởi động…" }
    return "Bấm cat để bắt đầu Voice"
  }

  private var voiceStateColor: Color {
    switch agent.voiceDisplayPhase {
    case .wake, .wakeDetected, .listening, .thinking: return .orange
    case .transcript, .speaking, .acting: return .green
    case .error: return .red
    case .sleeping: break
    }
    if agent.voiceWakeDiagnostic != nil { return .orange }
    if let status = agent.voiceStatus, !status.voice.available {
      return .red
    }
    if !agent.pttTranscript.isEmpty { return .green }
    return .secondary
  }

  var body: some View {
    HStack(alignment: .bottom, spacing: 8) {
      if agent.liveLogEnabled {
        LiveLogPanel(entries: agent.liveLogEntries)
      }

      if let answerText {
        AnswerBubble(
          text: answerText,
          onOpenMain: onOpenMain,
          onInteraction: { isInteracting in
            if isInteracting {
              bubbleDismissTask?.cancel()
            } else {
              scheduleBubbleDismissal()
            }
          }
        )
          .transition(.opacity.combined(with: .scale(scale: 0.96, anchor: .trailing)))
          .onAppear { scheduleBubbleDismissal() }
          .onChange(of: answerText) { _ in scheduleBubbleDismissal() }
      }

      VStack(spacing: 8) {
        CompanionCharacterImage(state: state)
          .fixedSize()
          .scaleEffect(reaction ? 1.04 : 1)
          .opacity(reaction ? 0.86 : 1)
          .animation(.easeOut(duration: 0.16), value: reaction)
          .accessibilityLabel(state.accessibilityLabel)
          .onTapGesture { react() }

        HStack(spacing: 4) {
          Image(systemName: "waveform")
            .accessibilityHidden(true)
          Text(voiceStateText)
        }
          .font(.caption)
          .foregroundStyle(voiceStateColor)
          .lineLimit(2)
          .multilineTextAlignment(.center)
          .frame(maxWidth: 210)
          .accessibilityLabel(voiceStateText)

        HStack(spacing: 6) {
          Button("Mở chat") { onOpenMain() }
            .buttonStyle(.borderless)
          if #available(macOS 14.0, *) {
            CompanionSettingsButton()
          } else {
            Button("Cài đặt") {
              openSettings()
            }
            .buttonStyle(.borderless)
          }
          Button("Thoát JL", role: .destructive) { NSApp.terminate(nil) }
            .buttonStyle(.borderless)
        }
        .font(.caption)
      }
      .onHover { isHovering = $0 }
    }
    .padding(8)
    .animation(.easeOut(duration: 0.16), value: state)
    .onReceive(Timer.publish(every: 0.25, on: .main, in: .common).autoconnect()) { _ in
      let isActive = agent.voiceStatus?.voice.active == true
        || agent.isWorking
        || agent.companionAnswer != nil
      if isActive {
        lastActivityAt = Date()
        isSleeping = false
      } else if Date().timeIntervalSince(lastActivityAt) >= 60 {
        isSleeping = true
      }
    }
    .onDisappear { bubbleDismissTask?.cancel() }
    .overlay(alignment: .topTrailing) {
      if isHovering && state != .idle {
        Text(state.accessibilityLabel)
          .font(.caption2)
          .padding(.horizontal, 7)
          .padding(.vertical, 4)
          .background(.thinMaterial, in: Capsule())
          .offset(y: -5)
          .transition(.opacity)
          .allowsHitTesting(false)
      }
    }
  }

  private func openSettings() {
    NSApp.activate(ignoringOtherApps: true)
    DispatchQueue.main.async {
      NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
    }
  }

  private func react() {
    lastActivityAt = Date()
    isSleeping = false
    withAnimation { reaction = true }
    agent.toggleVoiceSession()
    Task { @MainActor in
      try? await Task.sleep(for: .milliseconds(180))
      withAnimation { reaction = false }
    }
  }

  private func scheduleBubbleDismissal() {
    bubbleDismissTask?.cancel()
    bubbleDismissTask = Task { @MainActor in
      try? await Task.sleep(for: .seconds(10))
      guard !Task.isCancelled else { return }
      agent.dismissCompanionAnswer()
    }
  }
}

@available(macOS 14.0, *)
private struct CompanionSettingsButton: View {
  @Environment(\.openSettings) private var openSettings

  var body: some View {
    Button("Cài đặt") { openSettings() }
      .buttonStyle(.borderless)
  }
}

private struct AnswerBubble: View {
  let text: String
  let onOpenMain: () -> Void
  let onInteraction: (Bool) -> Void
  @State private var isHovering = false

  var body: some View {
    VStack(alignment: .leading, spacing: 7) {
      Text(text)
        .font(.system(size: 13, weight: .regular, design: .rounded))
        .foregroundStyle(.white.opacity(0.94))
        .lineLimit(8)
        .textSelection(.enabled)
      if isHovering {
        Button("Mở chi tiết") { onOpenMain() }
          .buttonStyle(.link)
          .font(.caption)
          .foregroundStyle(.white.opacity(0.82))
      }
    }
    .padding(13)
    .frame(width: 230, alignment: .leading)
    .background(.black.opacity(0.78), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    .overlay {
      RoundedRectangle(cornerRadius: 16, style: .continuous)
        .stroke(.white.opacity(0.12), lineWidth: 0.5)
    }
    .shadow(color: .black.opacity(0.24), radius: 16, y: 7)
    .onHover {
      isHovering = $0
      onInteraction($0)
    }
  }
}
