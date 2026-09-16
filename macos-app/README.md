# JL Agent macOS client

This directory contains the Phase 4A native SwiftUI control/consent client.
It is a client of the Python JL runtime and contains no Hermes runtime,
provider, tool implementation, or execution bypass.

The project is a dependency-free Swift package. The supported build lane uses
the installed Command Line Tools compiler with the matching `MacOSX15.5.sdk`
and a project-local SwiftPM/Clang module cache:

```bash
cd macos-app
./Scripts/run-native-tests.sh
./Scripts/build-app.sh
```

The scripts use these defaults explicitly and fail closed when the SDK is
missing. Override only the SDK path when a matching toolchain is installed:

```bash
JL_SWIFT_SDKROOT=/path/to/MacOSX15.5.sdk ./Scripts/build-app.sh
```

`JL_CODE_SIGN_IDENTITY` is optional for the development app. Without it the
app is ad-hoc signed; when set, it must name a valid Apple Development
identity. The voice runtime always requires a valid Apple Development
identity through `JL_VOICE_CODE_SIGN_IDENTITY`.

The build script creates an ad-hoc-signed development app at
`macos-app/.build/release/JL Agent.app`. Generated `.build` content is ignored.

## JL Voice Runtime signing

`JL Voice Runtime.app` is a separate, least-privilege microphone permission
target. Its current executable is deliberately inert: it does not open an
audio device or request TCC. The build refuses ad-hoc signing so the eventual
microphone authorization is tied to a stable JL-owned Apple team identity.

Install an eligible Apple Development identity for local development, then
build with its exact Keychain name:

```bash
export JL_VOICE_CODE_SIGN_IDENTITY="Apple Development: Example (TEAMID)"
./Scripts/build-voice-runtime.sh
```

The resulting app uses bundle and code identifier
`com.jlagent.voice-runtime`, Hardened Runtime, the audio-input entitlement, and
an explicit microphone usage description. The script verifies the entire code
tree with `--deep --strict`, rejects a missing Team ID or entitlement, and emits
the designated requirement for inspection. The current bundle has no nested
runtime; when Hermes is packaged later, every nested code object must be signed
inside-out with the same JL team identity. Do not launch it for live capture
until the separate Microphone/TCC gate is approved.

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
