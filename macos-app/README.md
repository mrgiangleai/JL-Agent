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
identity. The voice runtime may also be ad-hoc signed for this personal Mac;
macOS may request Microphone permission again after a rebuild.

The build script creates an ad-hoc-signed development app at
`macos-app/.build/release/JL Agent.app`. Generated `.build` content is ignored.

## JL Voice Runtime signing

`JL Voice Runtime.app` is a separate, least-privilege microphone permission
target. Its executable launches the packaged JL runtime with the existing
Hermes voice boundary enabled for explicit voice or wake actions. Hermes
continues to own capture, VAD, STT, wake detection, TTS, and voice-turn
behavior; the Swift host adds no audio engine or second control surface.

Install an eligible Apple Development identity for local development, then
build with its exact Keychain name:

```bash
export JL_VOICE_CODE_SIGN_IDENTITY="Apple Development: Example (TEAMID)"
./Scripts/build-voice-runtime.sh
```

For this personal v1, omit `JL_VOICE_CODE_SIGN_IDENTITY` to use an ad-hoc
development signature. Stable Apple signing, Developer ID, notarization, and
distribution remain deferred.

The resulting app uses bundle and code identifier
`com.jlagent.voice-runtime`, Hardened Runtime, the audio-input entitlement, and
an explicit microphone usage description. The script verifies the entire code
tree with `--deep --strict`, rejects a missing Team ID or entitlement, and emits
the designated requirement for inspection. The bundle includes the packaged JL
runtime and pinned Hermes revision. Launch voice or wake only after the user
has granted Microphone permission through normal macOS TCC. JL never changes or
bypasses that permission.

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
