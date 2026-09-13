# JL Agent macOS client

This directory contains the Phase 4A native SwiftUI control/consent client.
It is a client of the Python JL runtime and contains no Hermes runtime,
provider, tool implementation, or execution bypass.

The project is a dependency-free Swift package so it can be validated with the
installed Command Line Tools:

```bash
cd macos-app
swift run JLAgentNativeTests
./Scripts/build-app.sh
```

The build script creates an ad-hoc-signed development app at
`macos-app/.build/release/JL Agent.app`. Generated `.build` content is ignored.

On first launch, the app creates its consent signing key in Keychain and writes
only the public key to the private JL runtime directory. Start or restart the
foreground `jl-agent-runtime` after that provisioning step. The client imports
the existing private runtime IPC credential into Keychain; it never displays or
logs the credential.

Credential rotation is explicit and requires a stopped runtime:

```bash
jl-agent-runtime --rotate-credential
```

Then choose **Refresh Credential** in the native app before reconnecting.
