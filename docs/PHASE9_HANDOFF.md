# Phase 9 Handoff — Personal Daily-Driver v1

**Status:** CLOSED — personal-v1 implementation complete; deferred signing/distribution remain outside scope
**Checkpoint:** Final Phase 9 closeout after `1adc465`
**Target:** This Mac and this user account only

## Final result

`JL Agent.app` starts the packaged foreground JL runtime and keeps the existing
authenticated AF_UNIX, consent, Hermes, TCC, and Phase 6 boundaries intact.
Text chat remains the primary usable path. Ordinary readiness is cheap; the
CuaDriver and automation checks are explicit optional refreshes, while Voice
and Skills remain explicit-action paths. Main status is presented as
`Đang kết nối`, `Sẵn sàng`, or `Có vấn đề`.

The app-owned runtime controller cleans up its owned child on process teardown.
It never stops a runtime owned by another process. No Hermes source or
submodule change was made.

## Validation

- `tests.test_client_surface` and `tests.test_runtime_service`: **18/18**
- Python compilation: passed
- Swift compile-only parse for app/core/voice sources: passed
- `git diff --check`: passed
- Packaged release build with nested `JL Voice Runtime.app`: passed
- No live voice/CUA run, dependency installation, TCC modification, or
  signing/distribution work was performed.

## Personal-v1 launch notes

Launch the packaged artifact:

```text
macos-app/.build/arm64-apple-macosx/release/JL Agent.app
```

Text chat is ready when the main status shows `Sẵn sàng`. Voice still needs
normal user-granted Microphone permission for `com.jlagent.voice-runtime` and
may need a new grant after rebuilds. CuaDriver remains dependent on its own
official identity and Accessibility/Screen Recording grants. Those are not
blockers for personal text-chat use.

Phase 9 is closed out here. Stable Apple signing, TCC persistence across
rebuilds, notarization, distribution, wake-word dependency installation, and
new capabilities remain deferred. No new implementation phase is planned.
