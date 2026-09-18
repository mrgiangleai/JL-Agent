# Phase 9 Hermes-only Voice handoff

**Status:** ACCEPTED — real-mic Voice milestone
**Date:** 2026-09-18 ICT
**Hermes:** `0.21.2`, revision `044a77b3b6af4ce16138d42762f812a20b9f7a89`

## Result

JL Agent has one active Voice path:

```text
cat click -> JL session adapter -> Hermes native Voice -> Hermes -> Hermes native TTS
```

Hermes owns microphone capture, VAD, STT, response routing, TTS, follow-up
listening, and playback lifecycle. JL owns only authenticated start/stop,
event projection, Companion state, transcript display, and persisted Voice
settings.

The accepted packaged behavior is:

- Vietnamese Voice works through the Hermes path.
- Live/partial transcript updates while speaking; the complete final transcript
  replaces it immediately at utterance completion.
- VAD endpoint/settings and follow-up timeout work without changing the native
  Hermes Voice engine.
- Follow-up turns remain conversational and do not require a new start action.
- Genuine inactivity, timeout, or cat click stops Hermes and releases the mic.
- Duplicate Voice/UI ownership was removed; exactly one status/transcript and
  one session start/stop path remain.
- PersonalJarvis is fully removed from project code, build/runtime packaging,
  dependencies, submodules, and app-owned runtime state. Historical Phase 1
  audit documents are retained as history only.

## Validation

- Real-mic end-to-end acceptance: **PASS**.
- Python regression: `212` tests, **PASS**.
- Native Swift test binary: **PASS**.
- Repeated packaged Voice start/stop smoke: **PASS**; each run returned to
  sleeping with the mic released.
- Packaged root `JL Agent.app` release build and strict code-sign verification:
  **PASS**.
- Packaged runtime readiness: **PASS**; Hermes revision marker matched the
  pinned revision above.
- Phase 6 Hermes patch: preserved and not modified by this milestone.

## Future boundary

Do not change Voice behavior or optimize latency from this checkpoint. Any
future Hermes upgrade must first compare native Voice defaults, directly test
the candidate outside JL and through the packaged app, then re-audit and
rebase the intentional Phase 6 patch. Keep JL as a thin adapter and do not
reintroduce a second Voice engine or PersonalJarvis runtime.
