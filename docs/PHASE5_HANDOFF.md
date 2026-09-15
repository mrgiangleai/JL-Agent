# Phase 5 handoff — Voice and Wake Word

Date: 2026-09-15 ICT

## Status

Phase 5 is complete for the approved local-development scope. Do not rerun the
accepted live smoke, enable voice tools, or begin Phase 6 without explicit
authorization.

## Read first

1. `docs/PHASE5_REPORT.md`
2. `docs/PHASE5_HERMES_AUDIT.md`
3. `docs/SECURITY_MODEL.md`
4. `docs/ARCHITECTURE.md`
5. `docs/PROGRESS.md`

## Contracts to preserve

- Hermes is the only voice, wake, STT, and TTS implementation. Keep
  `upstream/hermes-agent` pinned and unmodified.
- JL alone owns feature activation, the separate live-audio approval gate,
  authenticated caller/session ownership, event visibility, and wake-phrase
  promotion.
- Every voice turn must keep `toolsets=[]` and
  `use_config_toolsets=False`. Voice tools are disabled in Phase 5.
- Raw PCM/audio must not cross IPC or enter the audit ledger.
- `hey j l` is the live-verified fallback default. A new custom phrase must
  pass the existing wake test before it can be persisted.
- Manual **Call JL** must remain available and independent of wake detection.
- The macOS `say` adapter must retain explicit WAVE/LEI16@22050 output unless a
  separately tested Hermes-compatible fix supersedes it.
- Do not weaken TCC. The temporary Codex Microphone grant is development-only;
  production must use the prepared stable, signed `com.jlagent.voice-runtime`
  host after an eligible Apple signing identity exists.

## Accepted proof

One bounded attempt, no retry:

`HEY J L -> wake PASS -> capture PASS -> local STT PASS -> deterministic response PASS -> TTS playback PASS`

The transcript contained 11 characters, TTS produced 109,506 bytes, no
provider/paid call ran, and `tool_execution_enabled` was `false`.

## Reproduce deterministic validation only

```bash
.venv/bin/ruff check src tests
.venv/bin/ty check
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests
cd macos-app
swift run -c release JLAgentNativeTests
./Scripts/build-app.sh
```

Current accepted results are 126 Python tests and 16 native contract tests,
plus the release app build/sign check. These commands do not require live audio
or a model/provider call.

## Git and upstream baseline

The closeout commit contains the final default and documentation. At handoff,
`main` must equal `origin/main`, the worktree must be clean, and Hermes must be
clean at `044a77b3b6af4ce16138d42762f812a20b9f7a89`.

## Next boundary

Stop before Phase 6. Production signing/TCC hardening is deferred work, not
permission to expand Phase 5 or alter the prepared signed-host architecture.
