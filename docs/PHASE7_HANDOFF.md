# Phase 7 handoff - Unified JL Assistant Loop

Date: 2026-09-16 ICT

## Status

Phase 7 implementation complete. The deterministic typed and voice admission
boundary is validated, but live voice E2E is **NOT PASS**. The test host
exposed zero audio devices, so live validation stopped before wake/STT. No
JL, Hermes, or AssistantAdmission failure was observed.

Validation totals:

- 195 Python tests passed.
- 16 native tests passed.
- Shared typed/voice `AssistantAdmission` was validated deterministically.

## Contract to preserve

- Typed Ask JL and Phase 5 voice transcripts enter one authenticated
  `assistant-request` / `AssistantAdmission` path.
- Admission only delegates. It does not become a second agent, planner,
  executor, router, or scheduler.
- Conversation uses `HermesTextOnlyTurnRunner` with tools disabled.
- Ambiguous or unsafe action-like input returns `clarification_required` and
  never falls through to Hermes conversation.
- Existing consent, policy, execution, computer-use, and scheduler gates are
  authoritative.
- Phase 5 HEY J L, STT, TTS, wake ownership, and behavior are unchanged.
- Hermes remains pinned and unmodified by Phase 7.
- Preserve the intentional Phase 6 three-file Hermes authorization patch when
  moving or upgrading the submodule.

## Live validation boundary

One bounded Phase 7 voice attempt was allowed. It did not reach wake
detection: the host exposed zero audio devices. The chain
`HEY J L -> STT -> AssistantAdmission -> Hermes/OpenAI Codex ->
conversation_completed -> TTS` is therefore **not marked PASS**. No tool
execution occurred. Revalidate later on an audio-capable host; do not treat
this environment blocker as an admission or Hermes failure.

## Runtime and security notes

- Smoke runtime homes must be short internal-APFS paths because AF_UNIX has a
  path-length limit.
- Keep runtime directories at `0700` and audit files at `0600`.
- The smoke helper retains sanitized child stdout/stderr and reports
  `failure_layer=runtime-startup` before cleanup.
- Do not weaken TCC, consent, policy, or tool gates. Do not retry live voice
  or inference automatically.

## Closeout

Phase 7 is closed. No voice retry, next phase, Hermes change, scheduler,
computer action, or external action was started during closeout.
