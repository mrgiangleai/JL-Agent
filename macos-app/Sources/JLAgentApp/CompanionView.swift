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
  case idle, listening, thinking, working, success, attention, error, sleeping

  var assetName: String { rawValue }

  var accessibilityLabel: String {
    switch self {
    case .idle: "JL đang sẵn sàng"
    case .listening: "JL đang lắng nghe"
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
    if agent.voiceStatus?.voice.active == true || agent.voiceStatus?.wake.active == true {
      return .listening
    }
    if agent.isWorking {
      return agent.decision == "ALLOW" || agent.resultText.contains("executing")
        ? .working : .thinking
    }
    if isSleeping { return .sleeping }
    if agent.companionAnswer != nil { return .success }
    return .idle
  }

  private var answerText: String? { agent.companionAnswer }

  private var voiceHelp: String {
    if let status = agent.voiceStatus, !status.voice.available {
      return status.voice.details ?? "Microphone or speech-to-text is unavailable."
    }
    return agent.voiceStatus?.voice.active == true ? "Dừng nghe" : "Nói với JL"
  }

  var body: some View {
    HStack(alignment: .bottom, spacing: 8) {
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

      VStack(spacing: 5) {
        CompanionCharacterImage(state: state)
          .fixedSize()
          .scaleEffect(reaction ? 1.04 : 1)
          .opacity(reaction ? 0.86 : 1)
          .animation(.easeOut(duration: 0.16), value: reaction)
          .accessibilityLabel(state.accessibilityLabel)
          .onTapGesture { react() }
          .contextMenu {
            Button("Nói với JL") { agent.toggleVoiceFromCompanion() }
            Button("Mở JL") { onOpenMain() }
          }

        Button {
          agent.toggleVoiceFromCompanion()
        } label: {
          Image(systemName: agent.voiceStatus?.voice.active == true ? "stop.fill" : "mic.fill")
            .font(.system(size: 12, weight: .semibold))
            .frame(width: 28, height: 28)
        }
        .buttonStyle(.borderedProminent)
        .tint(agent.voiceStatus?.voice.active == true ? .red : .accentColor)
        .help(voiceHelp)
        .accessibilityLabel(agent.voiceStatus?.voice.active == true ? "Dừng nghe" : "Nói với JL")
      }
      .onHover { isHovering = $0 }
    }
    .padding(8)
    .animation(.easeOut(duration: 0.16), value: state)
    .onReceive(Timer.publish(every: 1, on: .main, in: .common).autoconnect()) { _ in
      let isActive = agent.voiceStatus?.voice.active == true
        || agent.voiceStatus?.wake.active == true
        || agent.isWorking
        || agent.companionAnswer != nil
      if isActive {
        lastActivityAt = Date()
        isSleeping = false
      } else if Date().timeIntervalSince(lastActivityAt) >= 60 {
        isSleeping = true
      }
      if agent.voiceStatus?.voice.active == true || agent.voiceStatus?.wake.active == true {
        Task { await agent.refreshVoice() }
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

  private func react() {
    lastActivityAt = Date()
    isSleeping = false
    withAnimation { reaction = true }
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
