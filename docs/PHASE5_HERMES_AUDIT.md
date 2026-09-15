# Phase 5 Hermes voice and wake audit

Date: 2026-09-14 ICT

## Accepted boundary

Phase 5 reuses the exact pinned Hermes checkout at
`044a77b3b6af4ce16138d42762f812a20b9f7a89`. JL remains the only authority for
whether voice/wake may start and which authenticated caller/session may observe
or control it. Spoken input may produce a Hermes text response, but the voice
turn has an explicit empty toolset. It cannot execute tools or bypass the
existing JL request, policy, consent, and execution path.

Voice and wake are off by default. Actual capture additionally requires an
explicit activation gate. No dependency/model installation, microphone access,
or TCC prompt is part of deterministic validation.

## Reused Hermes components

- `tools/voice_mode.py`: microphone recording, VAD, bounded recording,
  transcription, stop-phrase and hallucination handling, playback, and
  dependency/readiness probing.
- `hermes_cli/voice.py`: process-wide continuous voice ownership, transcript
  callbacks, stop handling, TTS coordination, and echo avoidance.
- `tools/wake_word.py`: off-by-default wake configuration, one process/machine
  listener lease, local microphone capture, pause/resume around a voice turn,
  cooldown, dead-microphone diagnostics, and requirement status.
- `tools/wake_word_engines.py`: Hermes' openWakeWord, Sherpa, and Porcupine
  engine selection. JL does not add another detector.
- `tools/wakewords/hey_hermes.tflite` and `hey_hermes.onnx`: the bundled wake
  model artifacts. Apple Silicon resolves to Hermes' TFLite path.
- `hermes_cli.oneshot._run_agent`: the existing model/provider construction and
  turn path, invoked with `toolsets=[]` and `use_config_toolsets=False`.

The Hermes TUI gateway voice RPC is not embedded because it is a second control
surface with its own session/tool lifecycle. JL instead uses a narrow adapter
over the underlying Hermes APIs behind the existing authenticated AF_UNIX
boundary.

## Minimal implementation

1. A Python `VoiceCoordinator` owns the feature/activation gates, one exact
   caller/session lease, state transitions, and a bounded in-memory event queue.
2. A lazy `HermesVoiceBackend` delegates capture, VAD, STT, wake, and TTS to the
   pinned Hermes modules without importing voice dependencies at JL startup.
3. Transcript callbacks run a Hermes text-only turn. Responses may be spoken by
   Hermes, but tools are structurally disabled for this Phase.
4. Authenticated protocol-v1 operations are limited to `voice-status`,
   `voice-start`, `voice-stop`, `voice-events`, `wake-start`, `wake-stop`,
   `wake-test-start`, and `wake-phrase-set`.
   Unknown payload fields fail closed; transcript/reply events are visible only
   to the owning caller/session. Raw PCM/audio and voice content never enter the
   security audit ledger.
5. The native app remains an IPC client. It never imports Hermes, captures PCM,
   invokes a tool, modifies TCC, or owns microphone permission.
6. Settings tests a candidate phrase before promotion. Custom phrases reuse
   Hermes' Sherpa KWS path and fail closed when its optional local model is
   absent; no implicit model download occurs. Manual **Call JL** bypasses wake.

## Pre-live boundary

Hermes' optional voice packages are not JL's default application dependencies.
Dependency/model installation and each macOS Microphone step were therefore
held behind separate user approvals. Cloud STT/TTS, paid calls, full-duplex
streaming, LaunchAgent startup, and voice-driven tool execution remained out of
scope. The final approved local smoke evidence is recorded below.

## Approved local model setup

The pre-live model step was separately approved and completed without opening
an audio device:

- Local STT uses Hermes' default multilingual Faster-Whisper `base` model on
  Apple Silicon CPU/int8. Hugging Face revision
  `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66` is cached under the ignored
  project-local `.jl-agent/models/huggingface/` directory (166 MB).
- Wake detection uses the existing pinned
  `tools/wakewords/hey_hermes.tflite` model. Only openWakeWord's required
  `melspectrogram.tflite`, `embedding_model.tflite`, and `silero_vad.onnx`
  auxiliary assets were downloaded into the project `.venv`; no other wake
  phrases or duplicate ONNX feature models were fetched.
- Offline construction succeeded for Faster-Whisper with
  `local_files_only=True` and for Hermes' TFLite wake engine with network
  download replaced by a failing test seam. The engine exposed only the
  `hey_hermes` label.

Runtime composition sets `HF_HOME` to the project-local model root before any
Hermes voice imports. This setup was completed before any approved live
microphone activation.

## Sherpa custom wake candidate

The approved English custom-wake setup uses Hermes' pinned
`sherpa-onnx==1.13.4`, `sentencepiece==0.2.2`, `sounddevice==0.5.5`, and
`numpy==2.4.3`. The official FP32 GigaSpeech KWS runtime subset is stored only
under the ignored project-local `.jl-agent/models/sherpa/` cache. JL pins the
official archive provenance plus the exact size and SHA-256 of all five runtime
assets in `config/models/sherpa-gigaspeech-kws-fp32.json`; the adapter verifies
every asset before it delegates engine construction to Hermes.

Settings is prefilled with `HEY J L`. The phrase remained a candidate until it
passed both the authenticated test-before-default gate and the final bounded
live smoke. It is now JL's fallback default. Manual **Call JL** remains
independent of wake detection, and voice turns continue to pass an explicit
empty toolset.

Deterministic engine construction exposed an upstream dependency defect:
Sherpa 1.13.4's `text2token` imports `pypinyin` even for English-only BPE, while
Hermes' `wake.sherpa` pin set does not include it. The separately approved
compatibility dependency is exactly `pypinyin==0.55.0`; its source wheel size
and SHA-256 are pinned beside the model manifest. No other package was added.

After that wheel was verified and installed, offline construction passed for
the FP32 `KeywordSpotter` and its stream. The generated entry for the candidate
is `▁HE Y ▁ J ▁ L @HEY_J_L`. Construction did not call Hermes' listener or open
an audio device.

## Accepted final live proof

After explicit temporary Microphone approval for Codex, exactly one final
bounded smoke ran with no automatic retry:

`HEY J L -> wake -> voice capture -> local STT -> response -> TTS playback`

All five stages passed. The local transcript contained 11 characters and the
Hermes macOS `say` command adapter rendered and played 109,506 bytes of audio.
The short response was deterministic, so this test made no provider or paid
call. `tool_execution_enabled` was `false` throughout.

The macOS `/usr/bin/say` adapter required an explicit WAVE/PCM format because
the default voice could not infer an output data format. JL now supplies
`--file-format=WAVE --data-format=LEI16@22050` through Hermes' existing command
TTS provider; Hermes remains the TTS engine owner.

The temporary development TCC grant belongs to the Codex-responsible Python
accessor and is not the production trust boundary. The prepared
`com.jlagent.voice-runtime` host remains intact, fail-closed, and awaiting an
eligible Apple Development or Developer ID signing identity before production
Microphone enrollment.
