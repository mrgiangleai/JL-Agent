# Phase 7 report - Unified JL Assistant Loop

Date: 2026-09-16 ICT

## Outcome

Phase 7 implementation complete for the unified JL admission boundary. Typed
requests and Phase 5 voice transcripts use the same authenticated
`assistant-request` path and `AssistantAdmission`.

Live voice E2E is **NOT marked PASS**. Validation was blocked because the test
host exposed zero audio devices. Read-only host checks returned no devices from
both the audio enumeration path and `system_profiler SPAudioDataType`. No
JL, Hermes, or AssistantAdmission failure was observed.

## Implemented boundary

- `AssistantAdmission` is admission/delegation only; it is not an agent,
  planner, or executor.
- Typed Ask JL and voice transcripts share the same authenticated backend
  admission operation.
- Conversation delegation uses `HermesTextOnlyTurnRunner` with Hermes tools
  disabled.
- Ambiguous or unsafe action-like input fails closed as
  `clarification_required`; it never falls through to Hermes conversation.
- Existing consent, policy, execution, computer-use, and scheduler gates
  remain authoritative. No mutating computer-use or scheduler action was
  added to the voice path.
- Phase 5 HEY J L, STT, TTS, wake ownership, and voice behavior remain
  unchanged. No Hermes source or provider implementation was changed.
- The intentional Phase 6 three-file Hermes authorization patch remains
  preserved at the pinned Hermes revision; Phase 7 did not alter it.
- The Phase 7 smoke harness retains sanitized runtime startup output and
  strict APFS `0700` runtime-directory / `0600` audit-file checks. A short
  internal-APFS path is required for AF_UNIX compatibility.

## Deterministic validation

- **195 Python tests passed.**
- **16 native tests passed.**
- Shared typed/voice `AssistantAdmission` behavior, fail-closed handling,
  Hermes tool disabling, IPC contracts, and timeout contracts were validated
  deterministically.

## Live voice status

The single bounded live voice attempt did not reach wake detection because the
host reported zero audio devices. Therefore the chain

`HEY J L -> STT -> AssistantAdmission -> Hermes/OpenAI Codex ->
conversation_completed -> TTS`

is not claimed as a live pass, and no tool execution occurred. Revalidate the
voice E2E later on an audio-capable host. Do not infer a JL or Hermes defect
from this host-level blocker.

## Phase boundary

Phase 7 is closed without another voice or inference retry. The next authorized
step is a later bounded voice revalidation on an audio-capable host. No next
phase was started.
