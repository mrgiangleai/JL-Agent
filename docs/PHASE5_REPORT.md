# Phase 5 final report — Voice and Wake Word

Date: 2026-09-15 ICT

## Outcome

Phase 5 is complete for the approved local-development scope. JL reuses the
pinned Hermes voice stack and keeps JL as the authority for activation,
authenticated session ownership, wake-phrase promotion, and tool execution.
Voice-driven tool execution remains structurally disabled.

Exactly one final bounded live attempt passed:

`HEY J L -> wake -> capture -> local STT -> response -> TTS playback`

| Stage | Result | Evidence |
|---|---|---|
| Wake | PASS | Hermes Sherpa KWS detected `HEY J L`. |
| Capture | PASS | Hermes opened the already-approved local microphone path. |
| STT | PASS | Local Faster-Whisper produced an 11-character transcript. |
| Response | PASS | A short deterministic response was produced; no provider or paid call. |
| TTS | PASS | Hermes' macOS command provider rendered and played 109,506 audio bytes. |

There was no automatic retry. The result reported
`tool_execution_enabled: false`.

## Implemented boundary

- `VoiceCoordinator` is off by default, requires a separate activation gate,
  binds one exact caller/session, and exposes only bounded in-memory events.
- `HermesVoiceBackend` delegates capture, VAD, local STT, wake detection,
  pause/resume, stop handling, and TTS to the pinned Hermes checkout.
- Spoken transcripts enter Hermes through an explicit empty toolset
  (`toolsets=[]`, `use_config_toolsets=False`).
- Raw audio never crosses JL IPC and never enters the security audit ledger.
- The SwiftUI app remains an authenticated control/status client. Manual
  **Call JL** always starts listening without wake detection.
- Wake phrases are user-configurable and must pass a session-owned detection
  test before persistence. The live-verified fallback default is `hey j l`.

## Local dependencies and models

The approved Phase 5 additions to the project `.venv` include the Hermes
voice/KWS packages and the separately approved compatibility package:
`sherpa-onnx==1.13.4`,
`sentencepiece==0.2.2`, `sounddevice==0.5.5`, `numpy==2.4.3`, and
`pypinyin==0.55.0`. STT uses the project-local Faster-Whisper multilingual
`base` model.

Sherpa uses the official FP32 GigaSpeech KWS model. The official archive and
all five runtime assets are size/SHA-256 pinned in
`config/models/sherpa-gigaspeech-kws-fp32.json` and verified before engine
construction. The generated keyword entry is
`▁HE Y ▁ J ▁ L @HEY_J_L`.

## macOS TTS correction

Hermes' `/usr/bin/say` command provider previously failed with
`Opening output file failed: fmt?` because the default macOS voice did not infer
a usable output data format. JL now supplies the minimum adapter configuration:

`--file-format=WAVE --data-format=LEI16@22050`

Standalone render, playback, and integrated Hermes playback passed. Hermes
remains the TTS engine; no alternate engine or architecture was introduced.

## Validation

- 126 Python tests passed.
- 16 native Swift contract tests passed in release mode.
- Release `JL Agent.app` build and strict code-sign verification passed (ad-hoc
  development signature).
- Ruff, ty, `pip check`, voice dependency imports, Sherpa asset size/SHA-256,
  and `git diff --check` passed.
- Hermes remained clean at
  `044a77b3b6af4ce16138d42762f812a20b9f7a89`.

The full Hermes suite was not run. The focused JL checks exercise the new
boundary without provider calls or additional live audio.

## Trust and deferred production work

The final smoke used the user's explicitly approved temporary Microphone grant
for the Codex-responsible Python accessor. This is suitable only for personal
development and is not a production TCC identity.

The prepared inert `JL Voice Runtime.app` architecture remains unchanged. Its
build/signing workflow requires an eligible Apple Development or Developer ID
identity and rejects ad-hoc signing. No such identity is currently available,
so production signed-host enrollment remains deferred. No Microphone access was
granted broadly to Terminal or a generic Python application by JL code, and JL
does not automate or modify TCC.

## Phase boundary

Phase 5 stops here. Phase 6 requires a new explicit brief.
