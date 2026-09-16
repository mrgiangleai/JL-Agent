import SwiftUI

@main
struct JLAgentDesktopApp: App {
  @StateObject private var viewModel = AgentViewModel()

  var body: some Scene {
    WindowGroup("JL Agent") {
      ContentView(viewModel: viewModel)
        .frame(minWidth: 820, minHeight: 760)
        .task { viewModel.initialize() }
    }
    .windowResizability(.contentMinSize)

    Settings {
      WakePhraseSettingsView(viewModel: viewModel)
        .frame(width: 460)
        .padding()
    }
  }
}
