# Hermes Voice Strategy

Date: 2026-09-18 ICT

## Current decision

JL pins Hermes revision `044a77b3b6af4ce16138d42762f812a20b9f7a89`
(Hermes `0.21.2`). The approved Phase 6 Hermes patch remains in that dirty
submodule and is not changed by the Voice work.

The active Voice baseline is deliberately native:

`cat click -> Hermes voice.start_continuous -> Hermes text admission -> Hermes voice.speak_text`

The cat click is the only JL Voice control. JL owns the authenticated session
lease and bounded UI event projection. Hermes owns the microphone, recorder,
VAD, STT, stop phrases, continuous re-arm, silence termination, and TTS.
JL does not set a wake phrase, STT language, silence duration, VAD rule, TTS
provider, local acknowledgement, barge-in listener, echo filter, or audio
workaround. Idle means Hermes' Voice loop is stopped and the microphone is
closed.

The native API is called with callbacks only; no optional behavior arguments
are overridden. Hermes' own defaults therefore remain the source of truth for
endpointing and conversational follow-up behavior.

## Pin and upgrade policy

Hermes `0.21.2` is sufficient for the native baseline: the real Mac input
stream opened successfully through both the project runtime and the packaged
runtime path, and native Hermes `speak_text` completed playback. No Hermes
upgrade is included.

Before adopting a future upstream revision:

1. Compare `hermes_cli/voice.py` and `tools/voice_mode.py` against the pinned
   revision, including default function arguments and process-wide ownership.
2. Test the candidate directly outside JL with the real microphone and native
   TTS, then test the unchanged path through the packaged root app.
3. Rebase and reverse-check the intentional Phase 6 patch in
   `cron/execution_policy.py`, `cron/scheduler.py`, and
   `cron/scheduler_script.py` before changing the Hermes pin.
4. Run focused JL tests, native contract tests, package/signing checks, and
   repeated Voice start/stop/restart tests.
5. Adopt only if the candidate preserves the native contract and removes JL
   adapter code or provides a demonstrated upstream Voice fix. Never add a
   second Voice engine or a JL-specific behavior layer to compensate for an
   upstream difference.
