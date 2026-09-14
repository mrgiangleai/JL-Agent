# Phase 4A handoff

## Read this first

Phase 4A is the current boundary. Do not redo Phases 1-4A and do not begin
Phase 4B without a new explicit brief. Read in this order:

1. `docs/PHASE4A_REPORT.md`
2. `docs/PHASE4A_TRUST_BOUNDARY.md`
3. `docs/ARCHITECTURE.md`
4. `docs/SECURITY_MODEL.md`
5. `docs/PHASE3B_HANDOFF.md`
6. `docs/PROGRESS.md`

## Source state to revalidate

- Branch: `main`, tracking `origin/main`.
- Required clean Hermes revision:
  `044a77b3b6af4ce16138d42762f812a20b9f7a89` (`0.21.2`).
- Expected closeout: local `main == origin/main`, clean working tree, clean
  Hermes submodule. Revalidate rather than trusting this document.

## Implemented boundary

```text
SwiftUI request/status/activity client
  -> Keychain-backed authenticated protocol-v1 AF_UNIX
  -> JL policy -> ALLOW | CONFIRM | DENY
  -> runtime-owned exact pending consent
  -> native Keychain RSA signed approve/reject on private consent socket
  -> runtime-owned exact one-time approval consumption
  -> existing Phase 3B preparation and final execution gate
  -> thin Hermes adapter -> pinned Hermes
```

The native app never receives a fingerprint algorithm, wildcard grant, raw
approval token, Hermes tool registry, provider loop, or execution authority.

## Important paths

- `macos-app/Sources/JLAgentCore/`: IPC, protocol, Keychain, and consent client.
- `macos-app/Sources/JLAgentApp/`: minimal SwiftUI window and consent sheet.
- `macos-app/Sources/JLAgentNativeTests/`: deterministic native checks.
- `src/jl_agent/control/consent.py`: pending exact consent and RSA verification.
- `src/jl_agent/control/request_state.py`: normal status/activity/prepare/execute.
- `src/jl_agent/runtime_service.py`: two private sockets, readiness, rotation.
- `docs/PHASE4A_REPORT.md`: design, tests, limitations, and Phase 4B advice.

## Security contracts to preserve

- Normal IPC cannot issue or activate approvals.
- `permissions.py` remains the only action-fingerprint authority.
- Consent IPC accepts only an opaque ID, exact decision, and signature.
- Approval is exact, short-lived, one-time, caller/session-bound, and internal.
- Reject, cancel, expiry, replay, invalid signature, or changed action cannot
  execute.
- Native source cannot import/invoke Hermes; Python remains execution authority.
- Status/activity require normal authentication and expose only safe metadata.
- No credential, private key, raw approval, arguments, user content, or hidden
  reasoning may enter UI activity or logs.
- Preserve protocol-v1 size/timeout/identity checks and both private sockets.

## Validation commands

Use Python 3.11-3.13 and run sequentially:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check src tests scripts/verify-native-ipc-smoke.py
.venv/bin/ty check
.venv/bin/python -m pip check
cd macos-app
xcrun swift-format lint -r Sources Package.swift
swift run JLAgentNativeTests
./Scripts/build-app.sh
```

On this host, Swift must use the installed compatible SDK because the default
Command Line Tools SDK/compiler pair is mismatched:

```bash
SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX15.5.sdk \
CLANG_MODULE_CACHE_PATH="$PWD/.build/ModuleCache" \
SWIFTPM_MODULECACHE_OVERRIDE="$PWD/.build/ModuleCache" <swift command>
```

The cross-process smoke script needs a new short path directly under
`/private/tmp` because this checkout volume does not support Unix sockets. It
uses a fake Hermes runtime and deletes only its exact temporary directory plus
its test Keychain entries. It passed at Phase 4A closeout after explicit
permission was granted; request permission again before any future run that
touches a new outside-project path.

```bash
.venv/bin/python scripts/verify-native-ipc-smoke.py \
  macos-app/.build/arm64-apple-macosx/debug/JLAgentNativeTests \
  /private/tmp/jl-agent-phase4a-<unique>
```

## Deferred boundary

Do not begin Phase 4B from this handoff without an explicit brief. Production
code-signing/enrollment, lifecycle/onboarding, LaunchAgent, voice, wake word,
computer control, OS permission flows, local models, cloud services, accounts,
telemetry, and distribution remain deferred.
