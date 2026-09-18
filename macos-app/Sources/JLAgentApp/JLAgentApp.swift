import AppKit
import SwiftUI

@main
struct JLAgentDesktopApp: App {
  @NSApplicationDelegateAdaptor(JLAgentAppDelegate.self) private var appDelegate
  @StateObject private var viewModel: AgentViewModel
  @StateObject private var companionController: CompanionWindowController

  init() {
    let viewModel = AgentViewModel()
    let companionController = CompanionWindowController(agent: viewModel)
    _viewModel = StateObject(wrappedValue: viewModel)
    _companionController = StateObject(wrappedValue: companionController)
    appDelegate.runtimeStopper = { [weak viewModel] in
      viewModel?.stopRuntimeForTermination()
    }
    DispatchQueue.main.async {
      viewModel.initialize()
      companionController.show()
    }
  }

  var body: some Scene {
    WindowGroup("JL Agent", id: "main") {
      mainWindowContent
    }
    .windowResizability(.contentMinSize)

    Settings {
      WakePhraseSettingsView(viewModel: viewModel)
        .frame(width: 460)
        .padding()
    }

    MenuBarExtra("JL Agent", systemImage: "sparkles") {
      CompanionMenuContent()
    }
  }

  private var mainWindowContent: some View {
    ContentView(viewModel: viewModel)
      .frame(width: 640, height: 520)
      .onReceive(NotificationCenter.default.publisher(for: .jlOpenMainWindow)) { _ in
        configureMainWindow()
        NSApp.windows.first(where: { $0.title == "JL Agent" })?.makeKeyAndOrderFront(nil)
      }
      .onAppear { configureMainWindow() }
  }

  private func configureMainWindow() {
    DispatchQueue.main.async {
      NSApp.windows.first(where: { $0.title == "JL Agent" })?.setContentSize(
        NSSize(width: 640, height: 520)
      )
    }
  }
}

private struct CompanionMenuContent: View {
  @Environment(\.openWindow) private var openWindow

  var body: some View {
    Button("Mở JL") {
      NSApp.activate(ignoringOtherApps: true)
      openWindow(id: "main")
    }
    Button("Cài đặt") {
      NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
    }
    Divider()
    Button("Thoát JL Agent") { NSApp.terminate(nil) }
  }
}
