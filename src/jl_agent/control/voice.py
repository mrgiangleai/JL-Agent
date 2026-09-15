"""Thin, fail-closed JL ownership layer over pinned Hermes voice and wake APIs."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import threading
from collections import deque
from collections.abc import Callable, Mapping
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast


def verify_sherpa_model_assets(
    model_dir: Path, manifest_path: Path, expected_model: str
) -> None:
    """Fail closed unless every JL-pinned Sherpa runtime asset matches."""

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("schema_version") != 1
            or manifest.get("model") != expected_model
        ):
            raise ValueError("unexpected manifest")
        assets = manifest["runtime_assets"]
        if not isinstance(assets, list) or not assets:
            raise ValueError("missing runtime assets")
        for asset in assets:
            relative = asset["path"]
            if not isinstance(relative, str) or Path(relative).name != relative:
                raise ValueError("invalid asset path")
            path = model_dir / relative
            details = path.lstat()
            if not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid():
                raise ValueError("untrusted asset")
            if details.st_size != asset["size"]:
                raise ValueError("asset size mismatch")
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != asset["sha256"]:
                raise ValueError("asset digest mismatch")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("custom wake phrase model integrity check failed") from error


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
    def start_wake(self, *, on_wake: Callable[[], None], phrase: str) -> None: ...
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
        wake_phrase_path: Path | None = None,
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
        self._wake_phrase_path = wake_phrase_path
        self._wake_phrase = self._load_wake_phrase()
        self._tested_phrase: str | None = None
        self._wake_test_active = False

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
        if operation == "wake-test-start":
            return self.start_wake_test(
                caller_id, session_id, self._phrase_payload(payload)
            )
        if operation == "wake-phrase-set":
            return self.set_wake_phrase(
                caller_id, session_id, self._phrase_payload(payload)
            )
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
            dict(voice_requirements) if isinstance(voice_requirements, Mapping) else {}
        )
        wake_status = (
            dict(wake_requirements) if isinstance(wake_requirements, Mapping) else {}
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
                    "phrase": self._wake_phrase,
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
                if self._wake_active:
                    try:
                        self._backend.resume_wake()
                    except Exception:
                        self._append("voice_error", code="wake_resume_failed")
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
                self._backend.start_wake(
                    on_wake=self._on_wake, phrase=self._wake_phrase
                )
            except Exception as error:
                self._release_if_idle()
                raise VoiceError("wake_start_failed") from error
            self._wake_active = True
            self._append("wake_status", status="listening")
        return self.status(caller_id, session_id)

    def start_wake_test(
        self, caller_id: str, session_id: str, phrase: str
    ) -> dict[str, object]:
        phrase = self._normalize_phrase(phrase)
        self._require_activation()
        with self._lock:
            self._claim(caller_id, session_id)
            if self._wake_active or self._voice_active:
                raise VoiceError("voice_busy")
            self._wake_test_active = True
            try:
                self._backend.start_wake(
                    on_wake=lambda: self._on_wake_test(phrase), phrase=phrase
                )
            except Exception as error:
                self._wake_test_active = False
                self._release_if_idle()
                raise VoiceError("wake_test_start_failed") from error
            self._wake_active = True
            self._append("wake_phrase_test", status="listening", text=phrase)
        return self.status(caller_id, session_id)

    def set_wake_phrase(
        self, caller_id: str, session_id: str, phrase: str
    ) -> dict[str, object]:
        phrase = self._normalize_phrase(phrase)
        with self._lock:
            if self._tested_phrase != phrase:
                raise VoiceError("wake_phrase_not_tested")
            self._require_owner(caller_id, session_id)
            self._save_wake_phrase(phrase)
            self._wake_phrase = phrase
            self._append("wake_phrase_saved", text=phrase)
            self._tested_phrase = None
            self._release_if_idle()
        return self.status(caller_id, session_id)

    def stop_wake(self, caller_id: str, session_id: str) -> dict[str, object]:
        with self._lock:
            self._require_owner(caller_id, session_id)
            try:
                self._backend.stop_wake()
            except Exception as error:
                raise VoiceError("wake_stop_failed") from error
            self._wake_active = False
            self._wake_test_active = False
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

    def shutdown(self) -> None:
        """Best-effort release of Hermes' process-wide microphone owners."""
        with self._lock:
            if self._voice_active:
                try:
                    self._backend.stop_voice()
                except Exception:
                    pass
            if self._wake_active:
                try:
                    self._backend.stop_wake()
                except Exception:
                    pass
            self._voice_active = False
            self._wake_active = False
            self._wake_test_active = False
            self._turn_active = False
            self._owner = None

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

    def _on_wake_test(self, phrase: str) -> None:
        with self._lock:
            if not self._wake_test_active:
                return
            self._tested_phrase = phrase
            self._wake_test_active = False
            self._wake_active = False
            self._append("wake_phrase_test", status="passed", text=phrase)
        try:
            self._backend.stop_wake()
        except Exception:
            with self._lock:
                self._append("voice_error", code="wake_test_stop_failed")

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

    def _load_wake_phrase(self) -> str:
        if self._wake_phrase_path is None:
            return "hey hermes"
        try:
            details = self._wake_phrase_path.lstat()
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.geteuid()
                or details.st_size > 1024
            ):
                return "hey hermes"
            value = json.loads(self._wake_phrase_path.read_text(encoding="utf-8"))
            return self._normalize_phrase(value["phrase"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return "hey hermes"

    def _save_wake_phrase(self, phrase: str) -> None:
        if self._wake_phrase_path is None:
            return
        self._wake_phrase_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self._wake_phrase_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"phrase": phrase}), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._wake_phrase_path)

    @classmethod
    def _phrase_payload(cls, payload: Mapping[str, Any]) -> str:
        if set(payload) != {"phrase"}:
            raise VoiceError("malformed_payload")
        return cls._normalize_phrase(payload["phrase"])

    @staticmethod
    def _normalize_phrase(value: object) -> str:
        if not isinstance(value, str):
            raise VoiceError("invalid_wake_phrase")
        phrase = " ".join(value.strip().lower().split())
        if not 2 <= len(phrase) <= 64 or not all(
            character.isalnum() or character in " '-" for character in phrase
        ):
            raise VoiceError("invalid_wake_phrase")
        return phrase

    def _append(self, kind: str, **fields: object) -> None:
        self._sequence += 1
        self._events.append({"sequence": self._sequence, "kind": kind, **fields})

    @staticmethod
    def _require_empty(payload: Mapping[str, Any]) -> None:
        if payload:
            raise VoiceError("malformed_payload")


class HermesVoiceBackend:
    """Lazy adapter over the pinned Hermes process-wide voice/wake singletons."""

    def __init__(
        self,
        hermes_root: Path,
        *,
        model_cache_root: Path | None = None,
        sherpa_manifest_path: Path | None = None,
    ) -> None:
        self.hermes_root = hermes_root
        self._model_cache_root = model_cache_root
        self._sherpa_manifest_path = sherpa_manifest_path
        self._wake_owner = object()
        if model_cache_root is not None:
            os.environ.setdefault(
                "HF_HOME",
                str(model_cache_root / "huggingface"),
            )

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

    def start_wake(self, *, on_wake, phrase: str) -> None:
        self._activate_import_path()
        wake_word = import_module("tools.wake_word")
        config = dict(wake_word.load_wake_word_config())
        config.update(enabled=True, capture="local", phrase=phrase)
        if phrase == "hey hermes":
            config.update(provider="openwakeword")
            config["openwakeword"] = {
                "model": "hey_hermes",
                "inference_framework": wake_word.default_inference_framework(),
            }
        else:
            engines = import_module("tools.wake_word_engines")
            if self._model_cache_root is None or self._sherpa_manifest_path is None:
                raise RuntimeError("custom wake phrase model is not installed")
            model_dir = (
                self._model_cache_root / "sherpa" / engines._SHERPA_KWS_MODEL_DIR
            )
            verify_sherpa_model_assets(
                model_dir,
                self._sherpa_manifest_path,
                engines._SHERPA_KWS_MODEL_DIR,
            )
            config.update(provider="sherpa", profile_routing=False)
            config["sherpa"] = {"model_dir": str(model_dir)}
        wake_word.start_listening(on_wake, owner=self._wake_owner, config=config)

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
