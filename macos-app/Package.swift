// swift-tools-version: 6.0

import PackageDescription

let package = Package(
  name: "JLAgent",
  platforms: [.macOS(.v13)],
  products: [
    .library(name: "JLAgentCore", targets: ["JLAgentCore"]),
    .executable(name: "JLAgentApp", targets: ["JLAgentApp"]),
    .executable(name: "JLVoiceRuntime", targets: ["JLVoiceRuntime"]),
    .executable(name: "JLAgentNativeTests", targets: ["JLAgentNativeTests"]),
  ],
  targets: [
    .target(
      name: "JLAgentCore",
      linkerSettings: [.linkedFramework("Security")]
    ),
    .executableTarget(
      name: "JLAgentApp",
      dependencies: ["JLAgentCore"],
      resources: [.process("Resources")]
    ),
    .executableTarget(name: "JLVoiceRuntime"),
    .executableTarget(
      name: "JLAgentNativeTests",
      dependencies: ["JLAgentCore"]
    ),
  ]
)
