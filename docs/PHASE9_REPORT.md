# Phase 9 Report — Personal Daily-Driver v1

**Status:** COMPLETE
**Target:** This Mac and this user account only
**Final implementation checkpoint:** `1adc465`
**Hermes pin:** `044a77b3b6af4ce16138d42762f812a20b9f7a89`

## Outcome

JL Agent personal daily-driver v1 is complete for the approved local scope.
`JL Agent.app` launches the packaged foreground runtime without Terminal,
connects through the authenticated AF_UNIX boundary, and preserves the
existing Hermes/OpenAI Codex text-chat path. The companion character and
response bubble are included in the packaged app.

The app keeps Hermes as the sole core and preserves the existing authentication,
consent, approval, execution, audit, TCC, and CuaDriver trust boundaries.
Phase 6 Hermes working-tree changes remain preserved and were not modified.

## Completed Phase 9 scope

- Packaged runtime launch, bounded readiness, safe stop/restart, duplicate-owner
  refusal, reconnect, Library state migration, and diagnostics locations.
- Supported local SwiftPM build lane and ad-hoc personal development signing.
- Existing JL voice-host functionality and fail-closed CuaDriver readiness
  projection, without changing TCC ownership.
- Minimal Settings/Diagnostics and explicit optional checks.
- Daily-use polish: cheap ordinary readiness, lazy CuaDriver/automation
  probes, explicit Voice/Skills actions, simple Vietnamese Ready/Problem state,
  and cleanup of app-owned runtime processes on teardown.

## Validation evidence

- Focused Step 2 client/runtime tests: **18/18 passed**.
- Python compilation, Swift source parsing, and `git diff --check`: passed.
- Packaged release `JL Agent.app` build with nested `JL Voice Runtime.app`:
  passed.
- Earlier packaged smoke at `9c90bc9`: companion cat visible, runtime reached
  ready over AF_UNIX, typed `JL, trả lời OK.` reached Hermes/OpenAI Codex, and
  `OK.` appeared in the companion bubble.
- No live voice/CUA run was performed in the Step 2 polish pass. No dependency
  installation, signing enrollment, TCC modification, or Hermes modification
  was performed.

## Launch and deferred items

Launch:

```text
macos-app/.build/arm64-apple-macosx/release/JL Agent.app
```

Personal text-chat use is not blocked by signing or distribution. Voice still
requires a normal user-granted Microphone permission for
`com.jlagent.voice-runtime`; macOS may request it again after a rebuild.
CuaDriver remains dependent on its official identity and its own Accessibility
and Screen Recording grants. JL does not bypass, mutate, or weaken TCC.

Stable Apple signing, Developer ID, notarization, distribution, persistent TCC
identity, wake-word dependency installation, and new capabilities remain
deferred by decision. They are not Phase 9 completion blockers for this
personal v1.

## Closeout state

The root repository is clean except for the intentional pre-existing dirty
Hermes submodule patch. No further Phase 9 implementation phase is planned.
