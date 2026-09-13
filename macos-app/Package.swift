// swift-tools-version: 6.0

import PackageDescription

let package = Package(
  name: "JLAgent",
  platforms: [.macOS(.v13)],
  products: [
    .library(name: "JLAgentCore", targets: ["JLAgentCore"]),
    .executable(name: "JLAgentApp", targets: ["JLAgentApp"]),
    .executable(name: "JLAgentNativeTests", targets: ["JLAgentNativeTests"]),
  ],
  targets: [
    .target(
      name: "JLAgentCore",
      linkerSettings: [.linkedFramework("Security")]
    ),
    .executableTarget(
      name: "JLAgentApp",
      dependencies: ["JLAgentCore"]
    ),
    .executableTarget(
      name: "JLAgentNativeTests",
      dependencies: ["JLAgentCore"]
    ),
  ]
)
