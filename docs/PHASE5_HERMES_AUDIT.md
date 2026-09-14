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
   `voice-start`, `voice-stop`, `voice-events`, `wake-start`, and `wake-stop`.
   Unknown payload fields fail closed; transcript/reply events are visible only
   to the owning caller/session. Raw PCM/audio and voice content never enter the
   security audit ledger.
5. The native app remains an IPC client. It never imports Hermes, captures PCM,
   invokes a tool, modifies TCC, or owns microphone permission.

## Deferred live boundary

Hermes' optional voice packages are not JL dependencies and are not installed
by this milestone. Some providers can lazy-install packages or fetch models at
first activation. Therefore no start operation or live microphone test may run
until the user separately approves the exact dependency/model action and macOS
Microphone permission step. Cloud STT/TTS, paid calls, alternate wake models,
full-duplex streaming, LaunchAgent startup, and voice-driven tool execution are
out of scope.

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
Hermes voice imports. Live microphone activation and macOS Microphone TCC remain
unattempted and require a separate approval.
