"""Minimal JL ownership adapter for the pinned Hermes native Voice API.

JL owns only the authenticated cat-click session lease and UI events. Capture,
VAD, STT, conversational restart, stop phrases, and TTS remain Hermes-owned.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import time
import wave
from collections import deque
from collections.abc import Callable, Mapping
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .ipc import PROTOCOL_VERSION, IPCRequestEnvelope, RequestHandler


_LOGGER = logging.getLogger(__name__)

# Hermes owns the recorder/VAD implementation.  These are the only endpoint
# values JL supplies because Hermes 0.21.2's process-wide continuous API does
# not read voice.silence_* from config.yaml itself.  The values are derived
# from the packaged MacBook microphone measurements: ambient p90 was ~514 RMS
# and the captured utterance p90 was ~635 RMS.  A profile-level Hermes voice
# override, when present, remains authoritative.
_MEASURED_VOICE_SILENCE_THRESHOLD = 600
_MEASURED_VOICE_SILENCE_DURATION = 1.0
_DEFAULT_FOLLOW_UP_TIMEOUT = 3.0
_PARTIAL_TRANSCRIPT_INTERVAL = 0.9
_PARTIAL_TRANSCRIPT_MIN_SECONDS = 0.7
_TRACE_STARTED_AT = time.monotonic()


def _voice_trace(stage: str, **fields: object) -> None:
    """Write bounded, non-sensitive packaged Voice diagnostics to runtime.log."""
    fields = {
        "elapsed_ms": round((time.monotonic() - _TRACE_STARTED_AT) * 1000, 1),
        **fields,
    }
    details = " ".join(
        f"{key}={str(value).replace(' ', '_')[:160]}"
        for key, value in fields.items()
    )
    print(f"jl_voice_trace stage={stage} {details}".rstrip(), flush=True)


class VoiceError(RuntimeError):
    """Stable error code safe to return over authenticated local IPC."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class VoiceBackend(Protocol):
    def requirements(self) -> Mapping[str, Any]: ...

    def start_voice(
        self,
        *,
        on_transcript: Callable[[str], None],
        on_partial: Callable[[str], None] | None = None,
        on_status: Callable[[str], None],
        on_silent_limit: Callable[[], None],
        on_stop_phrase: Callable[[str], None],
    ) -> None: ...

    def stop_voice(self) -> None: ...
    def speak(self, text: str) -> None: ...
    def voice_settings(self) -> Mapping[str, object]: ...
    def update_voice_settings(self, payload: Mapping[str, Any]) -> Mapping[str, object]: ...
    def start_mic_test(self) -> Mapping[str, object]: ...
    def mic_test_status(self) -> Mapping[str, object]: ...
    def stop_mic_test(self) -> Mapping[str, object]: ...
    def test_tts(self, language: str) -> Mapping[str, object]: ...


RunAsync = Callable[[Callable[[], None]], None]


def _daemon(task: Callable[[], None]) -> None:
    import threading

    threading.Thread(target=task, name="jl-voice-turn", daemon=True).start()


class VoiceCoordinator:
    """Authenticated click-to-start/stop ownership around Hermes Voice."""

    def __init__(
        self,
        *,
        backend: VoiceBackend,
        assistant_handler: RequestHandler,
        credential: str,
        timezone: str = "UTC",
        enabled: bool = False,
        activation_approved: bool = False,
        event_capacity: int = 100,
        run_async: RunAsync = _daemon,
    ) -> None:
        self._backend = backend
        self._assistant_handler = assistant_handler
        self._credential = credential
        self._timezone = timezone
        self._enabled = enabled
        self._activation_approved = activation_approved
        self._events: deque[dict[str, object]] = deque(maxlen=event_capacity)
        self._run_async = run_async
        import threading

        self._lock = threading.RLock()
        self._owner: tuple[str, str] | None = None
        self._voice_active = False
        self._turn_active = False
        self._sequence = 0
        self._follow_up_timer: threading.Timer | None = None

    def handle(
        self,
        operation: str,
        payload: Mapping[str, Any],
        caller_id: str,
        session_id: str,
    ) -> Mapping[str, object]:
        if operation == "voice-status":
            self._require_empty(payload)
            return self.status(caller_id, session_id)
        if operation == "voice-settings":
            self._require_empty(payload)
            self._require_activation()
            return dict(self._backend.voice_settings())
        if operation == "voice-settings-set":
            self._require_activation()
            return dict(self._backend.update_voice_settings(payload))
        if operation == "voice-mic-test-start":
            self._require_empty(payload)
            self._require_activation()
            return self._start_mic_test()
        if operation == "voice-mic-test-status":
            self._require_empty(payload)
            self._require_activation()
            return dict(self._backend.mic_test_status())
        if operation == "voice-mic-test-stop":
            self._require_empty(payload)
            self._require_activation()
            return dict(self._backend.stop_mic_test())
        if operation == "voice-tts-test":
            if set(payload).difference({"language"}):
                raise VoiceError("malformed_payload")
            self._require_activation()
            return dict(self._backend.test_tts(str(payload.get("language") or "auto")))
        if operation == "voice-start":
            self._require_empty(payload)
            return self.start_voice(caller_id, session_id)
        if operation == "voice-stop":
            self._require_empty(payload)
            return self.stop_voice(caller_id, session_id)
        if operation in {
            "wake-start",
            "wake-stop",
            "wake-test-start",
            "wake-phrase-set",
        }:
            raise VoiceError("wake_disabled")
        if operation == "voice-events":
            if set(payload).difference({"after", "limit"}):
                raise VoiceError("malformed_payload")
            after = payload.get("after", 0)
            limit = payload.get("limit", 50)
            return {"events": self.events(caller_id, session_id, after, limit)}
        raise VoiceError("unsupported_operation")

    def status(self, caller_id: str, session_id: str) -> dict[str, Any]:
        requirements = self._safe_requirements()
        voice_requirements = requirements.get("voice")
        voice_status = (
            dict(voice_requirements) if isinstance(voice_requirements, Mapping) else {}
        )
        with self._lock:
            current = self._owner == (caller_id, session_id)
            return {
                "enabled": self._enabled,
                "activation_approved": self._activation_approved,
                "owned_by_current_session": current,
                "tool_execution_enabled": False,
                "voice": {**voice_status, "active": self._voice_active and current},
                "wake": {
                    "available": False,
                    "disabled": True,
                    "active": False,
                },
            }

    def start_voice(self, caller_id: str, session_id: str) -> dict[str, object]:
        self._require_activation()
        with self._lock:
            self._claim(caller_id, session_id)
            if self._voice_active:
                return self.status(caller_id, session_id)
            _voice_trace("jl_start_requested", caller=caller_id[:64])
            try:
                self._backend.start_voice(
                    on_transcript=self._on_transcript,
                    on_partial=self._on_partial,
                    on_status=self._on_voice_status,
                    on_silent_limit=self._on_silent_limit,
                    on_stop_phrase=self._on_stop_phrase,
                )
            except Exception as error:
                _voice_trace(
                    "jl_start_failed",
                    error_type=type(error).__name__,
                )
                self._release_if_idle()
                raise VoiceError("voice_start_failed") from error
            self._voice_active = True
            self._append("voice_status", status="listening")
            _voice_trace("jl_voice_active")
        return self.status(caller_id, session_id)

    def stop_voice(self, caller_id: str, session_id: str) -> dict[str, object]:
        with self._lock:
            self._require_owner(caller_id, session_id)
            if self._voice_active:
                try:
                    self._backend.stop_voice()
                except Exception as error:
                    raise VoiceError("voice_stop_failed") from error
            self._voice_active = False
            self._turn_active = False
            self._cancel_follow_up_timeout_locked()
            self._append("voice_status", status="idle")
            self._release_if_idle()
        return self.status(caller_id, session_id)

    def events(
        self,
        caller_id: str,
        session_id: str,
        after: object,
        limit: object,
    ) -> list[dict[str, object]]:
        if (
            isinstance(after, bool)
            or not isinstance(after, int)
            or after < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise VoiceError("malformed_payload")
        with self._lock:
            if self._owner is not None:
                self._require_owner(caller_id, session_id)
            return [
                dict(item)
                for item in self._events
                if int(item["sequence"]) > after
            ][:limit]

    def shutdown(self) -> None:
        with self._lock:
            if self._voice_active:
                try:
                    self._backend.stop_voice()
                except Exception:
                    _LOGGER.debug("native Hermes Voice shutdown failed", exc_info=True)
            self._voice_active = False
            self._turn_active = False
            self._cancel_follow_up_timeout_locked()
            self._owner = None

    def _on_partial(self, transcript: str) -> None:
        text = transcript.strip()[:16_384]
        if not text:
            return
        with self._lock:
            if self._voice_active and not self._turn_active:
                self._append("partial_transcript", text=text)

    def _on_transcript(self, transcript: str) -> None:
        text = transcript.strip()[:16_384]
        if not text:
            _voice_trace("stt_empty_ignored")
            return
        _voice_trace("stt_final_received", chars=len(text))
        with self._lock:
            if not self._voice_active:
                return
            self._cancel_follow_up_timeout_locked()
            self._append("transcript", text=text)
            if self._turn_active:
                self._append("voice_error", code="voice_turn_busy")
                return
            self._turn_active = True
            owner = self._owner
        if owner is None:
            with self._lock:
                self._turn_active = False
                self._append("voice_error", code="voice_session_mismatch")
            return
        caller_id, session_id = owner

        def run() -> None:
            try:
                _voice_trace("assistant_dispatch", chars=len(text))
                response = self._assistant_handler(
                    IPCRequestEnvelope(
                        protocol_version=PROTOCOL_VERSION,
                        request_id=f"voice-{uuid4().hex}",
                        caller_id=caller_id,
                        session_id=session_id,
                        operation="assistant-request",
                        payload={
                            "text": text,
                            "input_mode": "voice",
                            "timezone": self._timezone,
                        },
                        credential=self._credential,
                    )
                )
                _voice_trace(
                    "assistant_response",
                    ok=response.ok,
                    error=response.error_code or "none",
                )
                result = dict(response.result or {})
                if not response.ok:
                    self._append_voice_result(response.error_code or "voice_turn_failed")
                    return
                if result.get("state") != "conversation_completed":
                    self._append_voice_result(str(result.get("state") or "voice_turn_failed"))
                    return
                reply = result.get("reply")
                if not isinstance(reply, str) or not reply.strip():
                    self._append_voice_result("voice_turn_failed")
                    return
                reply = reply.strip()[:32_768]
                with self._lock:
                    if not self._voice_active:
                        return
                    self._append("reply", text=reply)
                _voice_trace("tts_start", chars=len(reply))
                self._backend.speak(reply)
                _voice_trace("tts_complete")
                with self._lock:
                    if self._voice_active:
                        self._append("tts_playback_complete")
                        self._schedule_follow_up_timeout_locked()
            except Exception:
                _voice_trace("turn_failed", error_type="exception")
                _LOGGER.exception("native Hermes Voice turn failed")
                self._append_voice_result("voice_turn_failed")
            finally:
                with self._lock:
                    self._turn_active = False

        self._run_async(run)

    def _append_voice_result(self, code: str) -> None:
        with self._lock:
            self._append("voice_error", code=code[:64])

    def _on_voice_status(self, status: str) -> None:
        _voice_trace("hermes_status", status=str(status)[:64])
        with self._lock:
            self._append("voice_status", status=str(status)[:64])
            if status == "idle":
                self._cancel_follow_up_timeout_locked()

    def _on_stop_phrase(self, _phrase: str) -> None:
        _voice_trace("hermes_stop_phrase")
        with self._lock:
            self._voice_active = False
            self._append("voice_status", status="stopped_by_phrase")
            self._release_if_idle()

    def _on_silent_limit(self) -> None:
        _voice_trace("hermes_silent_limit")
        with self._lock:
            self._cancel_follow_up_timeout_locked()
            if not self._voice_active:
                return
            self._voice_active = False
            self._append("voice_status", status="idle")
            self._release_if_idle()

    def _start_mic_test(self) -> Mapping[str, object]:
        with self._lock:
            if self._voice_active:
                raise VoiceError("voice_busy")
        return self._backend.start_mic_test()

    def _schedule_follow_up_timeout_locked(self) -> None:
        self._cancel_follow_up_timeout_locked()
        try:
            timeout = float(self._backend.voice_settings().get(
                "follow_up_timeout", _DEFAULT_FOLLOW_UP_TIMEOUT
            ))
        except (TypeError, ValueError, AttributeError):
            timeout = _DEFAULT_FOLLOW_UP_TIMEOUT
        timer = threading.Timer(max(0.5, timeout), self._follow_up_expired)
        timer.daemon = True
        self._follow_up_timer = timer
        timer.start()

    def _cancel_follow_up_timeout_locked(self) -> None:
        if self._follow_up_timer is not None:
            self._follow_up_timer.cancel()
        self._follow_up_timer = None

    def _follow_up_expired(self) -> None:
        with self._lock:
            if not self._voice_active or self._turn_active:
                return
            self._voice_active = False
            self._append("voice_status", status="idle")
            self._release_if_idle()
            self._follow_up_timer = None
        _voice_trace("follow_up_timeout")
        try:
            self._backend.stop_voice()
        except Exception:
            _LOGGER.exception("follow-up timeout failed to stop Hermes Voice")

    def _safe_requirements(self) -> Mapping[str, Any]:
        try:
            return self._backend.requirements()
        except Exception:
            return {"voice": {"available": False, "details": "probe unavailable"}}

    def _require_activation(self) -> None:
        if not self._enabled:
            raise VoiceError("voice_disabled")
        if not self._activation_approved:
            raise VoiceError("voice_activation_not_approved")

    def _claim(self, caller_id: str, session_id: str) -> None:
        owner = (caller_id, session_id)
        if self._owner is not None and self._owner != owner:
            raise VoiceError("voice_session_mismatch")
        self._owner = owner

    def _require_owner(self, caller_id: str, session_id: str) -> None:
        if self._owner != (caller_id, session_id):
            raise VoiceError("voice_session_mismatch")

    def _release_if_idle(self) -> None:
        if not self._voice_active:
            self._owner = None

    def _append(self, kind: str, **fields: object) -> None:
        self._sequence += 1
        self._events.append({"sequence": self._sequence, "kind": kind, **fields})

    @staticmethod
    def _require_empty(payload: Mapping[str, Any]) -> None:
        if payload:
            raise VoiceError("malformed_payload")


class HermesVoiceBackend:
    """Direct, lazy adapter to pinned Hermes process-wide Voice functions."""

    def __init__(
        self,
        hermes_root: Path,
        *,
        model_cache_root: Path | None = None,
    ) -> None:
        self.hermes_root = hermes_root
        self._partial_stop = threading.Event()
        self._partial_thread: threading.Thread | None = None
        self._mic_test_recorder: Any = None
        self._mic_test_lock = threading.RLock()
        if model_cache_root is not None:
            import os

            os.environ.setdefault("HF_HOME", str(model_cache_root / "huggingface"))

    def requirements(self) -> Mapping[str, Any]:
        self._activate_import_path()
        voice_mode = import_module("tools.voice_mode")
        return {"voice": voice_mode.check_voice_requirements()}

    def start_voice(
        self,
        *,
        on_transcript: Callable[[str], None],
        on_partial: Callable[[str], None] | None = None,
        on_status: Callable[[str], None],
        on_silent_limit: Callable[[], None],
        on_stop_phrase: Callable[[str], None],
    ) -> None:
        self._activate_import_path()
        voice = import_module("hermes_cli.voice")
        hermes = import_module("hermes_cli")
        import os

        silence_threshold, silence_duration = self._endpoint_settings()

        _voice_trace(
            "hermes_native_start",
            python=sys.executable,
            hermes_module=getattr(hermes, "__file__", "unknown"),
            hermes_version=getattr(hermes, "__version__", "unknown"),
            hermes_root=self.hermes_root,
            hermes_home=os.environ.get("HERMES_HOME", "unset"),
            hf_home=os.environ.get("HF_HOME", "unset"),
        )
        _voice_trace(
            "hermes_endpoint_config",
            silence_threshold=silence_threshold,
            silence_duration=silence_duration,
        )

        def native_status(status: str) -> None:
            if status == "transcribing":
                self._stop_partial_transcription()
            elif status == "listening" and self._partial_thread is None:
                self._start_partial_transcription(voice, on_partial)
            on_status(status)

        voice.start_continuous(
            on_transcript=lambda text: self._final_transcript(text, on_transcript),
            on_status=native_status,
            on_silent_limit=on_silent_limit,
            on_stop_phrase=on_stop_phrase,
            silence_threshold=silence_threshold,
            silence_duration=silence_duration,
        )
        self._start_partial_transcription(voice, on_partial or (lambda _text: None))
        _voice_trace("hermes_native_started")

    def stop_voice(self) -> None:
        self._activate_import_path()
        self._stop_partial_transcription()
        _voice_trace("hermes_native_stop")
        import_module("hermes_cli.voice").stop_continuous(force_transcribe=False)
        _voice_trace("hermes_native_stopped")

    def speak(self, text: str) -> None:
        self._activate_import_path()
        _voice_trace("hermes_native_tts", chars=len(text))
        import_module("hermes_cli.voice").speak_text(text)
        _voice_trace("hermes_native_tts_returned")

    def voice_settings(self) -> Mapping[str, object]:
        self._activate_import_path()
        config_module = import_module("hermes_cli.config")
        raw = config_module.read_raw_config_readonly()
        voice_cfg = raw.get("voice", {}) if isinstance(raw, Mapping) else {}
        stt_cfg = raw.get("stt", {}) if isinstance(raw, Mapping) else {}
        voice_cfg = voice_cfg if isinstance(voice_cfg, Mapping) else {}
        stt_cfg = stt_cfg if isinstance(stt_cfg, Mapping) else {}
        language = stt_cfg.get("language", "")
        language = language if language in {"vi", "en"} else "auto"
        threshold, duration = self._endpoint_settings()
        follow_up = voice_cfg.get("follow_up_timeout", _DEFAULT_FOLLOW_UP_TIMEOUT)
        try:
            follow_up = float(follow_up)
        except (TypeError, ValueError):
            follow_up = _DEFAULT_FOLLOW_UP_TIMEOUT
        return {
            "language": language,
            "silence_threshold": threshold,
            "silence_duration": duration,
            "follow_up_timeout": max(0.5, min(60.0, follow_up)),
        }

    def update_voice_settings(self, payload: Mapping[str, Any]) -> Mapping[str, object]:
        allowed = {"language", "silence_threshold", "silence_duration", "follow_up_timeout"}
        if set(payload) != allowed:
            raise VoiceError("malformed_payload")
        language = payload.get("language")
        threshold = payload.get("silence_threshold")
        duration = payload.get("silence_duration")
        follow_up = payload.get("follow_up_timeout")
        if language not in {"auto", "vi", "en"}:
            raise VoiceError("invalid_voice_language")
        if (
            isinstance(threshold, bool) or not isinstance(threshold, (int, float))
            or not 200 <= float(threshold) <= 2000
        ):
            raise VoiceError("invalid_mic_sensitivity")
        if (
            isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not 0.5 <= float(duration) <= 5.0
        ):
            raise VoiceError("invalid_silence_duration")
        if (
            isinstance(follow_up, bool) or not isinstance(follow_up, (int, float))
            or not 0.5 <= float(follow_up) <= 60.0
        ):
            raise VoiceError("invalid_follow_up_timeout")
        self._activate_import_path()
        config_module = import_module("hermes_cli.config")
        raw = config_module.read_raw_config_readonly()
        if not isinstance(raw, dict):
            raw = {}
        stt_cfg = raw.setdefault("stt", {})
        voice_cfg = raw.setdefault("voice", {})
        if not isinstance(stt_cfg, dict) or not isinstance(voice_cfg, dict):
            raise VoiceError("voice_config_invalid")
        stt_cfg["language"] = "" if language == "auto" else language
        voice_cfg["silence_threshold"] = int(round(float(threshold)))
        voice_cfg["silence_duration"] = round(float(duration), 2)
        voice_cfg["follow_up_timeout"] = round(float(follow_up), 2)
        # Hermes providers own TTS. For the native Edge default, choose its
        # documented language voice when the user explicitly selects one.
        tts_cfg = raw.get("tts")
        if tts_cfg is None and language in {"vi", "en"}:
            tts_cfg = raw.setdefault("tts", {"provider": "edge"})
        if isinstance(tts_cfg, dict) and (tts_cfg.get("provider") or "edge") == "edge":
            edge_cfg = tts_cfg.setdefault("edge", {})
            if isinstance(edge_cfg, dict) and language in {"vi", "en"}:
                edge_cfg["voice"] = (
                    "vi-VN-HoaiMyNeural" if language == "vi" else "en-US-AriaNeural"
                )
        config_module.atomic_config_write(
            config_module.get_config_path(), raw, sort_keys=False
        )
        return self.voice_settings()

    def start_mic_test(self) -> Mapping[str, object]:
        self._activate_import_path()
        voice_mode = import_module("tools.voice_mode")
        with self._mic_test_lock:
            if self._mic_test_recorder is not None:
                return self.mic_test_status()
            try:
                recorder = voice_mode.create_audio_recorder()
                recorder.start()
            except Exception as error:
                raise VoiceError("mic_test_failed") from error
            self._mic_test_recorder = recorder
        _voice_trace("mic_test_started")
        return self.mic_test_status()

    def mic_test_status(self) -> Mapping[str, object]:
        with self._mic_test_lock:
            recorder = self._mic_test_recorder
            if recorder is None:
                return {"active": False, "level": 0, "state": "idle"}
            level = int(getattr(recorder, "_current_rms", 0) or 0)
            return {
                "active": bool(getattr(recorder, "is_recording", False)),
                "level": max(0, min(32767, level)),
                "state": "listening" if getattr(recorder, "is_recording", False) else "idle",
            }

    def stop_mic_test(self) -> Mapping[str, object]:
        with self._mic_test_lock:
            recorder, self._mic_test_recorder = self._mic_test_recorder, None
        if recorder is not None:
            try:
                recorder.cancel()
                recorder.shutdown()
            except Exception as error:
                raise VoiceError("mic_test_stop_failed") from error
        _voice_trace("mic_test_stopped")
        return self.mic_test_status()

    def test_tts(self, language: str) -> Mapping[str, object]:
        if language not in {"auto", "vi", "en"}:
            raise VoiceError("invalid_voice_language")
        text = "Xin chào, đây là bài kiểm tra giọng nói." if language == "vi" else "Hello, this is a voice test."
        self.speak(text)
        return {"ok": True, "language": language}

    def _final_transcript(self, text: str, callback: Callable[[str], None]) -> None:
        self._stop_partial_transcription()
        callback(text)

    def _start_partial_transcription(self, voice: Any, callback: Callable[[str], None]) -> None:
        self._stop_partial_transcription()
        self._partial_stop.clear()

        def run() -> None:
            self._partial_loop(voice, callback)

        self._partial_thread = threading.Thread(target=run, name="jl-voice-partial", daemon=True)
        self._partial_thread.start()

    def _stop_partial_transcription(self) -> None:
        self._partial_stop.set()
        thread = self._partial_thread
        self._partial_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.25)

    def _partial_loop(self, voice: Any, callback: Callable[[str], None]) -> None:
        voice_mode = import_module("tools.voice_mode")
        last_text = ""
        while not self._partial_stop.wait(_PARTIAL_TRANSCRIPT_INTERVAL):
            recorder = getattr(voice, "_continuous_recorder", None)
            if recorder is None:
                continue
            try:
                with recorder._lock:
                    if not getattr(recorder, "_recording", False):
                        continue
                    frames = list(getattr(recorder, "_frames", []))
                    sample_rate = int(getattr(recorder, "_sample_rate", 16000))
                if not frames:
                    continue
                audio = __import__("numpy").concatenate(frames, axis=0)
                if len(audio) < int(sample_rate * _PARTIAL_TRANSCRIPT_MIN_SECONDS):
                    continue
                wav_path: str | None = None
                with tempfile.NamedTemporaryFile(prefix="jl-voice-partial-", suffix=".wav", delete=False) as temp:
                    wav_path = temp.name
                with wave.open(wav_path, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(sample_rate)
                    wav.writeframes(audio.tobytes())
                result = voice_mode.transcribe_recording(wav_path)
                text = (result.get("transcript") or "").strip()
                if text and text != last_text and not voice_mode.is_whisper_hallucination(text):
                    last_text = text
                    if not self._partial_stop.is_set():
                        callback(text)
            except Exception:
                _LOGGER.debug("native Hermes partial transcript unavailable", exc_info=True)
            finally:
                if wav_path is not None:
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass

    def _activate_import_path(self) -> None:
        root = str(self.hermes_root)
        if root not in sys.path:
            sys.path.insert(0, root)

    @staticmethod
    def _endpoint_settings() -> tuple[int, float]:
        """Return Hermes' native endpoint knobs without implementing another VAD."""
        threshold = _MEASURED_VOICE_SILENCE_THRESHOLD
        duration = _MEASURED_VOICE_SILENCE_DURATION
        try:
            # load_config() merges Hermes defaults (200/3.0), which would hide
            # the measured fallback.  The upstream raw-read API preserves only
            # explicit user overrides and does not mutate the profile.
            config = import_module("hermes_cli.config").read_raw_config_readonly()
            voice_cfg = config.get("voice", {}) if isinstance(config, Mapping) else {}
            if isinstance(voice_cfg, Mapping):
                configured_threshold = voice_cfg.get("silence_threshold")
                if (
                    isinstance(configured_threshold, (int, float))
                    and not isinstance(configured_threshold, bool)
                    and configured_threshold > 0
                ):
                    threshold = int(configured_threshold)
                configured_duration = voice_cfg.get("silence_duration")
                if (
                    isinstance(configured_duration, (int, float))
                    and not isinstance(configured_duration, bool)
                    and configured_duration > 0
                ):
                    duration = float(configured_duration)
        except Exception:
            # Endpoint operation must retain the measured safe defaults if a
            # malformed/absent Hermes profile cannot be loaded.
            pass
        return threshold, duration


class HermesTextOnlyTurnRunner:
    """Run one Hermes text turn with an explicit empty toolset."""

    def __init__(self, hermes_root: Path) -> None:
        self.hermes_root = hermes_root

    def __call__(self, prompt: str) -> str:
        root = str(self.hermes_root)
        if root not in sys.path:
            sys.path.insert(0, root)
        oneshot = import_module("hermes_cli.oneshot")
        response, _ = oneshot._run_agent(
            prompt,
            toolsets=[],
            use_config_toolsets=False,
        )
        return response


def voice_enabled_from_environment() -> bool:
    import os

    return os.environ.get("JL_AGENT_VOICE_ENABLED") == "1"


def voice_activation_approved_from_environment() -> bool:
    import os

    return os.environ.get("JL_AGENT_VOICE_ACTIVATION_APPROVED") == "1"
