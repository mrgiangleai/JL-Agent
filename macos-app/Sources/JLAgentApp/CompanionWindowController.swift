import AppKit
import ServiceManagement
import SwiftUI

extension Notification.Name {
  static let jlOpenMainWindow = Notification.Name("JLAgent.openMainWindow")
}

@MainActor
final class CompanionWindowController: NSObject, ObservableObject, NSWindowDelegate {
  private static let positionXKey = "JLAgent.companion.origin.x"
  private static let positionYKey = "JLAgent.companion.origin.y"
  private static let windowSize = NSSize(width: 620, height: 400)

  let agent: AgentViewModel
  private var panel: NSPanel?

  init(agent: AgentViewModel) {
    self.agent = agent
  }

  func show() {
    let panel = panel ?? makePanel()
    panel.setFrameOrigin(restoredOrigin(for: panel))
    panel.orderFrontRegardless()
  }

  func hide() {
    panel?.orderOut(nil)
  }

  func windowDidMove(_ notification: Notification) {
    guard let panel else { return }
    let constrained = constrainedOrigin(panel.frame.origin, for: panel)
    if constrained != panel.frame.origin {
      panel.setFrameOrigin(constrained)
    }
    UserDefaults.standard.set(constrained.x, forKey: Self.positionXKey)
    UserDefaults.standard.set(constrained.y, forKey: Self.positionYKey)
  }

  private func makePanel() -> NSPanel {
    let hosting = CompanionHostingView(
      rootView: CompanionView(agent: agent) { [weak self] in
        self?.openMainWindow()
      }
    )
    hosting.autoresizingMask = [.width, .height]
    hosting.interactiveRegions = { [weak self] in self?.interactiveRegions() ?? [] }

    let panel = NSPanel(
      contentRect: NSRect(origin: .zero, size: Self.windowSize),
      styleMask: [.borderless, .nonactivatingPanel],
      backing: .buffered,
      defer: false
    )
    panel.isOpaque = false
    panel.backgroundColor = .clear
    panel.hasShadow = false
    panel.level = .floating
    panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
    panel.hidesOnDeactivate = false
    panel.isMovableByWindowBackground = true
    panel.delegate = self
    panel.contentView = hosting
    self.panel = panel
    return panel
  }

  private func openMainWindow() {
    NSApp.activate(ignoringOtherApps: true)
    if let mainWindow = NSApp.windows.first(where: { $0.title == "JL Agent" }) {
      mainWindow.makeKeyAndOrderFront(nil)
    } else {
      NotificationCenter.default.post(name: .jlOpenMainWindow, object: nil)
    }
  }

  private func restoredOrigin(for panel: NSPanel) -> NSPoint {
    let defaults = UserDefaults.standard
    if defaults.object(forKey: Self.positionXKey) != nil,
      defaults.object(forKey: Self.positionYKey) != nil
    {
      return constrainedOrigin(
        NSPoint(
          x: defaults.double(forKey: Self.positionXKey),
          y: defaults.double(forKey: Self.positionYKey)
        ),
        for: panel
      )
    }
    let visibleFrame = (NSScreen.main ?? NSScreen.screens.first)?.visibleFrame
      ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
    return NSPoint(
      x: visibleFrame.maxX - panel.frame.width - 24,
      y: visibleFrame.minY + 24
    )
  }

  private func constrainedOrigin(_ origin: NSPoint, for panel: NSPanel) -> NSPoint {
    let screen = NSScreen.screens.first {
      $0.visibleFrame.insetBy(dx: -1, dy: -1).contains(origin)
    } ?? NSScreen.main
    guard let visibleFrame = screen?.visibleFrame else { return origin }
    return NSPoint(
      x: min(max(origin.x, visibleFrame.minX + 8), visibleFrame.maxX - panel.frame.width - 8),
      y: min(max(origin.y, visibleFrame.minY + 8), visibleFrame.maxY - panel.frame.height - 8)
    )
  }

  private func interactiveRegions() -> [CGRect] {
    var regions = [CGRect(x: 248, y: 0, width: 372, height: 400)]
    if agent.companionAnswer != nil {
      regions.append(CGRect(x: 0, y: 0, width: 248, height: 400))
    }
    return regions
  }
}

@MainActor
private final class CompanionHostingView<Content: View>: NSHostingView<Content> {
  var interactiveRegions: () -> [CGRect] = { [] }

  override func hitTest(_ point: NSPoint) -> NSView? {
    guard interactiveRegions().contains(where: { $0.contains(point) }) else { return nil }
    return super.hitTest(point)
  }
}

@MainActor
final class JLAgentAppDelegate: NSObject, NSApplicationDelegate {
  var runtimeStopper: (() -> Void)?

  func applicationDidFinishLaunching(_ notification: Notification) {
    DispatchQueue.main.async {
      NSApp.windows
        .filter { $0.title == "JL Agent" }
        .forEach {
          $0.setContentSize(NSSize(width: 640, height: 520))
          $0.orderOut(nil)
        }
    }
    guard Bundle.main.bundleURL.pathExtension == "app" else { return }
    try? SMAppService.mainApp.register()
  }

  func applicationWillTerminate(_ notification: Notification) {
    runtimeStopper?()
  }
}
