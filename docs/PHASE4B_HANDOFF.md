# Phase 4B handoff

## Read this first

Phase 4B is complete. Do not redo Phases 1-4B and do not begin the next phase
without a new explicit brief. Read in this order:

1. `docs/PHASE4B_REPORT.md`
2. `docs/PHASE4B_HERMES_AUDIT.md`
3. `docs/ARCHITECTURE.md`
4. `docs/SECURITY_MODEL.md`
5. `docs/PHASE4A_HANDOFF.md`
6. `docs/PHASE3B_HANDOFF.md`
7. `docs/PROGRESS.md`

## Source state to revalidate

- Branch: `main`, tracking `origin/main`.
- Required clean Hermes revision:
  `044a77b3b6af4ce16138d42762f812a20b9f7a89` (`0.21.2`).
- Expected closeout: local `main == origin/main`, clean working tree, clean
  Hermes submodule. Revalidate rather than trusting this document.

## Implemented boundary

```text
SwiftUI permission/request/consent client
  -> authenticated protocol-v1 AF_UNIX
  -> runtime-owned cua-driver/TCC health
  -> JL exact inner-action risk policy
  -> native exact consent when required
  -> approval fingerprint with app/target/foreground identity
  -> final health/policy/route/foreground revalidation
  -> single-use execution gate
  -> HermesExecutionAdapter
  -> pinned Hermes computer_use handler
  -> cua-driver on a configured host
```

The native app never performs screenshot, mouse, keyboard, or accessibility
automation. Hermes remains the sole computer-use implementation.

## Important paths

- `docs/PHASE4B_REPORT.md`: full outcome, rules, validation, and limitations.
- `docs/PHASE4B_HERMES_AUDIT.md`: exact upstream source audit.
- `src/jl_agent/control/computer_use.py`: TCC/readiness projection and target
  guard.
- `src/jl_agent/control/permissions.py`: authoritative inner-action scope rules.
- `src/jl_agent/control/hermes_projection.py`: computer-use descriptor.
- `src/jl_agent/control/execution.py`: final target-validator hook.
- `macos-app/Sources/JLAgentCore/Models.swift`: strict status and request binding.
- `macos-app/Sources/JLAgentApp/ContentView.swift`: compact Permissions UI.
- `scripts/verify-native-ipc-smoke.py`: cross-process pinned-Hermes noop proof.

## Contracts to preserve

- Only Accessibility and Screen Recording are required by this pin; do not add
  Input Monitoring without a new source audit.
- False/unknown TCC never becomes granted. Health and input fail closed.
- Client health cannot override the trusted runtime probe.
- All computer actions use Hermes toolset/tool `computer_use`; no driver call is
  an alternate execution path.
- Protected reads and all input actions require the existing exact native
  consent; mutating actions cannot run unattended.
- App target, resolved target, current foreground identity, arguments, scopes,
  caller/session, and descriptor identity remain approval-bound.
- Revalidate foreground identity immediately before mutating dispatch. Preserve
  Hermes sticky target, element token, hard-block, approval, and verdict checks.
- Never enable `unrestricted`, YOLO, or a private daemon to bypass JL policy.
- Native source must contain no direct CGEvent, AXUIElement, screenshot, driver
  invocation, Hermes import, or tool execution surface.

## Validation commands

Use Python 3.11-3.13 and run sequentially:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check src tests scripts/verify-native-ipc-smoke.py
.venv/bin/ty check
.venv/bin/python -m pip check
cd macos-app
xcrun swift-format lint -r Sources Package.swift
SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX15.5.sdk \
CLANG_MODULE_CACHE_PATH="$PWD/.build/ModuleCache" \
SWIFTPM_MODULECACHE_OVERRIDE="$PWD/.build/ModuleCache" \
swift run JLAgentNativeTests
SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX15.5.sdk \
CLANG_MODULE_CACHE_PATH="$PWD/.build/ModuleCache" \
SWIFTPM_MODULECACHE_OVERRIDE="$PWD/.build/ModuleCache" \
./Scripts/build-app.sh
```

The cross-process proof needs a new exact `/private/tmp/jl-agent-phase4b-*`
directory because the checkout volume does not support Unix sockets. It deletes
only that directory and its test Keychain items. Obtain authorization before a
future run outside the project boundary.

## Host gap and next boundary

Real macOS computer use was not run because cua-driver/CuaDriver.app and their
TCC grants are absent. The deterministic path reached the pinned Hermes handler
with its noop backend; this is not live GUI confirmation.

Do not begin another phase from this handoff. A future explicitly scoped phase
should address production lifecycle/onboarding and stable signed-app consent
enrollment before broader computer workflows, voice, or distribution.
