# Phase 6 native UI checkpoint

Date: 2026-09-16 ICT

## Scope implemented

- Added authenticated `automation-*` IPC for the existing Hermes cron backend:
  status, list, create paused reminder, activation challenge, pause, remove,
  history and global Stop All.
- Added signed `automation-consent-decision` on the existing private consent
  socket. The payload carries only consent ID, decision and signature; approval
  IDs and binding fingerprints remain runtime-owned.
- Added native SwiftUI schedules panel:
  list, one-time/recurring paused creation, exact activation sheet, pause,
  remove, history/status, Stop All and disabled/stopped status text.

## Preserved boundary

- Hermes remains the scheduler owner. JL calls Hermes job/history APIs inside
  the selected profile scope and does not add a second scheduler.
- Jobs are fixed local `no_agent` reminders only, created paused by default.
- Activation requires signed native consent before the backend stores the JL
  grant and resumes the Hermes job.
- No provider, MCP, network monitor, external messaging, autonomous tool path,
  global resume or automatic retry was added.
- The previously reviewed Hermes three-file hook patch and Hermes pin remain
  unchanged.

## Validation

- Python: `177` tests passed with `python -B -m unittest`.
- Native contract harness: `18` tests passed with `swift run
  JLAgentNativeTests`.
- Swift app build: `swift build` passed.
- Release bundle: `macos-app/Scripts/build-app.sh` passed and ad-hoc signed
  `macos-app/.build/arm64-apple-macosx/release/JL Agent.app`.
- Static checks: Ruff, ty and `pip check` passed.
- Bounded GUI smoke: launched the local release app bundle and verified the
  `Schedules` panel renders with Refresh, Stop All, one-time/recurring controls,
  Create Paused, schedule list/status and empty-state display while runtime is
  unavailable. No live schedule was activated through GUI smoke.

## Stop boundary

Stopped before Phase 6 closeout. Scheduler live smoke remains the prior
single-run evidence in `PHASE6_SCHEDULER_LIVE_VALIDATION.md`; this checkpoint
validates the native/backend contract and bounded UI rendering only.
