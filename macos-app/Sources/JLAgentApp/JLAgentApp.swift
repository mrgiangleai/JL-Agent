import SwiftUI

@main
struct JLAgentDesktopApp: App {
  @StateObject private var viewModel = AgentViewModel()

  var body: some Scene {
    WindowGroup("JL Agent") {
      ContentView(viewModel: viewModel)
        .frame(minWidth: 720, minHeight: 620)
        .task { viewModel.initialize() }
    }
    .windowResizability(.contentMinSize)
  }
}
