"""Thin, fail-closed JL ownership layer over pinned Hermes voice and wake APIs."""

from __future__ import annotations

import os
import sys
import threading
from collections import deque
from collections.abc import Callable, Mapping
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast


class VoiceError(RuntimeError):
    """A stable error code safe to return over authenticated local IPC."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class VoiceBackend(Protocol):
    def requirements(self) -> Mapping[str, Any]: ...

    def start_voice(
        self,
        *,
        on_transcript: Callable[[str], None],
        on_status: Callable[[str], None],
        on_stop_phrase: Callable[[str], None],
    ) -> None: ...

    def stop_voice(self) -> None: ...
    def start_wake(self, *, on_wake: Callable[[], None]) -> None: ...
    def stop_wake(self) -> None: ...
    def pause_wake(self) -> None: ...
    def resume_wake(self) -> None: ...
    def speak(self, text: str) -> None: ...


RunAsync = Callable[[Callable[[], None]], None]


def _daemon(task: Callable[[], None]) -> None:
    threading.Thread(target=task, name="jl-voice-turn", daemon=True).start()


class VoiceCoordinator:
    """Own voice state, caller binding, and bounded transcript events.

    Hermes owns capture, VAD, STT, wake detection, and TTS. JL owns whether those
    capabilities may start and ensures spoken text reaches a tool-free agent turn.
    """

    def __init__(
        self,
        *,
        backend: VoiceBackend,
        turn_runner: Callable[[str], str],
        enabled: bool = False,
        activation_approved: bool = False,
        event_capacity: int = 100,
        run_async: RunAsync = _daemon,
    ) -> None:
        self._backend = backend
        self._turn_runner = turn_runner
        self._enabled = enabled
        self._activation_approved = activation_approved
        self._events: deque[dict[str, object]] = deque(maxlen=event_capacity)
        self._run_async = run_async
        self._lock = threading.RLock()
        self._owner: tuple[str, str] | None = None
        self._voice_active = False
        self._wake_active = False
        self._turn_active = False
        self._sequence = 0

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
        if operation == "voice-start":
            self._require_empty(payload)
            return self.start_voice(caller_id, session_id)
        if operation == "voice-stop":
            self._require_empty(payload)
            return self.stop_voice(caller_id, session_id)
        if operation == "wake-start":
            self._require_empty(payload)
            return self.start_wake(caller_id, session_id)
        if operation == "wake-stop":
            self._require_empty(payload)
            return self.stop_wake(caller_id, session_id)
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
        wake_requirements = requirements.get("wake")
        voice_status = (
            dict(voice_requirements)
            if isinstance(voice_requirements, Mapping)
            else {}
        )
        wake_status = (
            dict(wake_requirements)
            if isinstance(wake_requirements, Mapping)
            else {}
        )
        with self._lock:
            current = self._owner == (caller_id, session_id)
            return {
                "enabled": self._enabled,
                "activation_approved": self._activation_approved,
                "owned_by_current_session": current,
                "tool_execution_enabled": False,
                "voice": {
                    **voice_status,
                    "active": self._voice_active and current,
                },
                "wake": {
                    **wake_status,
                    "active": self._wake_active and current,
                },
            }

    def start_voice(self, caller_id: str, session_id: str) -> dict[str, object]:
        self._require_activation()
        with self._lock:
            self._claim(caller_id, session_id)
            if self._voice_active:
                return self.status(caller_id, session_id)
            if self._wake_active:
                self._backend.pause_wake()
            try:
                self._backend.start_voice(
                    on_transcript=self._on_transcript,
                    on_status=self._on_voice_status,
                    on_stop_phrase=self._on_stop_phrase,
                )
            except Exception as error:
                self._release_if_idle()
                raise VoiceError("voice_start_failed") from error
            self._voice_active = True
        return self.status(caller_id, session_id)

    def stop_voice(self, caller_id: str, session_id: str) -> dict[str, object]:
        with self._lock:
            self._require_owner(caller_id, session_id)
            try:
                self._backend.stop_voice()
                if self._wake_active:
                    self._backend.resume_wake()
            except Exception as error:
                raise VoiceError("voice_stop_failed") from error
            self._voice_active = False
            self._append("voice_status", status="idle")
            self._release_if_idle()
        return self.status(caller_id, session_id)

    def start_wake(self, caller_id: str, session_id: str) -> dict[str, object]:
        self._require_activation()
        with self._lock:
            self._claim(caller_id, session_id)
            if self._wake_active:
                return self.status(caller_id, session_id)
            try:
                self._backend.start_wake(on_wake=self._on_wake)
            except Exception as error:
                self._release_if_idle()
                raise VoiceError("wake_start_failed") from error
            self._wake_active = True
            self._append("wake_status", status="listening")
        return self.status(caller_id, session_id)

    def stop_wake(self, caller_id: str, session_id: str) -> dict[str, object]:
        with self._lock:
            self._require_owner(caller_id, session_id)
            try:
                self._backend.stop_wake()
            except Exception as error:
                raise VoiceError("wake_stop_failed") from error
            self._wake_active = False
            self._append("wake_status", status="idle")
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
            after_value = cast(int, after)
            limit_value = cast(int, limit)
            return [
                dict(item)
                for item in self._events
                if cast(int, item["sequence"]) > after_value
            ][:limit_value]

    def _on_transcript(self, transcript: str) -> None:
        text = transcript.strip()[:16_384]
        if not text:
            return
        with self._lock:
            if not self._voice_active:
                return
            self._append("transcript", text=text)
            if self._turn_active:
                self._append("voice_error", code="voice_turn_busy")
                return
            self._turn_active = True

        def run() -> None:
            try:
                reply = self._turn_runner(text).strip()[:32_768]
                if reply:
                    with self._lock:
                        self._append("reply", text=reply)
                    self._backend.speak(reply)
            except Exception:
                with self._lock:
                    self._append("voice_error", code="voice_turn_failed")
            finally:
                with self._lock:
                    self._turn_active = False

        self._run_async(run)

    def _on_voice_status(self, status: str) -> None:
        with self._lock:
            self._append("voice_status", status=str(status)[:64])

    def _on_stop_phrase(self, _phrase: str) -> None:
        with self._lock:
            self._voice_active = False
            self._append("voice_status", status="stopped_by_phrase")
            if self._wake_active:
                try:
                    self._backend.resume_wake()
                except Exception:
                    self._append("voice_error", code="wake_resume_failed")
            self._release_if_idle()

    def _on_wake(self) -> None:
        with self._lock:
            owner = self._owner
            self._append("wake_detected")
        if owner is not None:
            try:
                self.start_voice(*owner)
            except VoiceError as error:
                with self._lock:
                    self._append("voice_error", code=error.code)

    def _safe_requirements(self) -> Mapping[str, Any]:
        try:
            return self._backend.requirements()
        except Exception:
            return {
                "voice": {"available": False, "details": "probe unavailable"},
                "wake": {"available": False, "phrase": "", "hint": "probe unavailable"},
            }

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
        if not self._voice_active and not self._wake_active:
            self._owner = None

    def _append(self, kind: str, **fields: object) -> None:
        self._sequence += 1
        self._events.append({"sequence": self._sequence, "kind": kind, **fields})

    @staticmethod
    def _require_empty(payload: Mapping[str, Any]) -> None:
        if payload:
            raise VoiceError("malformed_payload")


class HermesVoiceBackend:
    """Lazy adapter over the pinned Hermes process-wide voice/wake singletons."""

    def __init__(self, hermes_root: Path) -> None:
        self.hermes_root = hermes_root
        self._wake_owner = object()

    def requirements(self) -> Mapping[str, Any]:
        self._activate_import_path()
        voice_mode = import_module("tools.voice_mode")
        wake_word = import_module("tools.wake_word")

        return {
            "voice": voice_mode.check_voice_requirements(),
            "wake": wake_word.check_wake_word_requirements(),
        }

    def start_voice(self, *, on_transcript, on_status, on_stop_phrase) -> None:
        self._activate_import_path()
        voice = import_module("hermes_cli.voice")
        voice.start_continuous(
            on_transcript=on_transcript,
            on_status=on_status,
            on_stop_phrase=on_stop_phrase,
        )

    def stop_voice(self) -> None:
        self._activate_import_path()
        import_module("hermes_cli.voice").stop_continuous(force_transcribe=False)

    def start_wake(self, *, on_wake) -> None:
        self._activate_import_path()
        wake_word = import_module("tools.wake_word")
        wake_word.start_listening(on_wake, owner=self._wake_owner)

    def stop_wake(self) -> None:
        self._activate_import_path()
        import_module("tools.wake_word").stop_listening(owner=self._wake_owner)

    def pause_wake(self) -> None:
        self._activate_import_path()
        import_module("tools.wake_word").pause_listening(owner=self._wake_owner)

    def resume_wake(self) -> None:
        self._activate_import_path()
        import_module("tools.wake_word").resume_listening(owner=self._wake_owner)

    def speak(self, text: str) -> None:
        self._activate_import_path()
        import_module("hermes_cli.voice").speak_text(text)

    def _activate_import_path(self) -> None:
        root = str(self.hermes_root)
        if root not in sys.path:
            sys.path.insert(0, root)


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
    return os.environ.get("JL_AGENT_VOICE_ENABLED") == "1"


def voice_activation_approved_from_environment() -> bool:
    return os.environ.get("JL_AGENT_VOICE_ACTIVATION_APPROVED") == "1"
