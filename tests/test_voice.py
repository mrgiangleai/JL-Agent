from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from jl_agent.control.ipc import IPCResponseEnvelope
from jl_agent.control.voice import (
    HermesTextOnlyTurnRunner,
    HermesVoiceBackend,
    VoiceCoordinator,
    VoiceError,
)


class FakeHermesVoiceBackend:
    def __init__(self) -> None:
        self.voice_callback = None
        self.partial_callback = None
        self.spoken: list[str] = []
        self.voice_starts = 0
        self.stops = 0

    def requirements(self) -> dict[str, object]:
        return {"voice": {"available": True, "details": "fake voice ready"}}

    def start_voice(
        self, *, on_transcript, on_partial=None, on_status, on_silent_limit, on_stop_phrase
    ) -> None:
        self.voice_starts += 1
        self.voice_callback = on_transcript
        self.partial_callback = on_partial
        on_status("listening")

    def stop_voice(self) -> None:
        self.stops += 1

    def speak(self, text: str) -> None:
        self.spoken.append(text)


class VoiceCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeHermesVoiceBackend()
        self.assistant_requests = []

        def assistant_request(envelope):
            self.assistant_requests.append(envelope)
            return IPCResponseEnvelope.success(
                envelope.request_id,
                {
                    "state": "conversation_completed",
                    "reply": f"reply: {envelope.payload['text']}",
                },
            )

        self.voice = VoiceCoordinator(
            backend=self.backend,
            assistant_handler=assistant_request,
            credential="voice-credential",
            enabled=True,
            activation_approved=True,
            run_async=lambda task: task(),
        )

    def test_start_requires_activation(self) -> None:
        disabled = VoiceCoordinator(
            backend=self.backend,
            assistant_handler=lambda _: IPCResponseEnvelope.failure("unused", "unused", "unused"),
            credential="voice-credential",
        )
        with self.assertRaisesRegex(VoiceError, "voice_disabled"):
            disabled.start_voice("caller", "session")

        gated = VoiceCoordinator(
            backend=self.backend,
            assistant_handler=lambda _: IPCResponseEnvelope.failure("unused", "unused", "unused"),
            credential="voice-credential",
            enabled=True,
        )
        with self.assertRaisesRegex(VoiceError, "voice_activation_not_approved"):
            gated.start_voice("caller", "session")

    def test_native_session_starts_directly_without_wake_or_ack(self) -> None:
        status = self.voice.start_voice("caller", "session")
        self.assertEqual(self.backend.voice_starts, 1)
        self.assertTrue(status["voice"]["active"])
        self.assertFalse(status["wake"]["active"])
        self.assertEqual(self.backend.spoken, [])

    def test_transcript_uses_shared_admission_and_native_tts(self) -> None:
        self.voice.start_voice("caller", "session")
        assert self.backend.voice_callback is not None
        self.backend.voice_callback("hello")

        request = self.assistant_requests[0]
        self.assertEqual(request.operation, "assistant-request")
        self.assertEqual(request.payload["text"], "hello")
        self.assertEqual(request.payload["input_mode"], "voice")
        self.assertEqual(request.credential, "voice-credential")
        self.assertEqual(self.backend.spoken, ["reply: hello"])
        self.assertEqual(
            [event["kind"] for event in self.voice.events("caller", "session", 0, 20)],
            [
                "voice_status",
                "voice_status",
                "transcript",
                "reply",
                "tts_playback_complete",
            ],
        )

    def test_empty_transcript_is_never_sent(self) -> None:
        self.voice.start_voice("caller", "session")
        assert self.backend.voice_callback is not None
        self.backend.voice_callback("  ")
        self.assertEqual(self.assistant_requests, [])

    def test_partial_transcript_is_ui_only_and_follow_up_timeout_releases_session(self) -> None:
        self.voice.start_voice("caller", "session")
        assert self.backend.partial_callback is not None
        self.backend.partial_callback("xin chào")
        partial = self.voice.events("caller", "session", 0, 20)
        self.assertEqual(partial[-1], {"sequence": 3, "kind": "partial_transcript", "text": "xin chào"})
        self.assertEqual(self.assistant_requests, [])

        assert self.backend.voice_callback is not None
        self.backend.voice_callback("xin chào")
        self.voice._follow_up_expired()
        status = self.voice.status("caller", "session")
        self.assertFalse(status["voice"]["active"])
        self.assertFalse(status["owned_by_current_session"])
        self.assertEqual(self.backend.stops, 1)

    def test_wake_operations_are_disabled(self) -> None:
        for operation in ("wake-start", "wake-stop", "wake-test-start", "wake-phrase-set"):
            with self.assertRaisesRegex(VoiceError, "wake_disabled"):
                self.voice.handle(operation, {}, "caller", "session")

    def test_stop_releases_native_session(self) -> None:
        self.voice.start_voice("caller", "session")
        status = self.voice.stop_voice("caller", "session")
        self.assertEqual(self.backend.stops, 1)
        self.assertFalse(status["voice"]["active"])
        self.assertFalse(status["owned_by_current_session"])

    def test_session_binding_blocks_cross_session_content(self) -> None:
        self.voice.start_voice("caller", "session")
        with self.assertRaisesRegex(VoiceError, "voice_session_mismatch"):
            self.voice.stop_voice("caller", "other-session")
        with self.assertRaisesRegex(VoiceError, "voice_session_mismatch"):
            self.voice.events("caller", "other-session", 0, 20)

    def test_hermes_backend_forwards_native_endpoint_settings(self) -> None:
        captured: dict[str, object] = {}
        voice = ModuleType("hermes_cli.voice")

        def start_continuous(**kwargs) -> None:
            captured.update(kwargs)

        spoken: list[str] = []
        voice.start_continuous = start_continuous  # type: ignore[attr-defined]
        voice.stop_continuous = lambda **kwargs: None  # type: ignore[attr-defined]
        voice.speak_text = lambda text: spoken.append(text)  # type: ignore[attr-defined]
        config = ModuleType("hermes_cli.config")
        config.read_raw_config_readonly = lambda: {}  # type: ignore[attr-defined]

        def import_voice_module(name: str):
            return config if name == "hermes_cli.config" else voice

        with patch("jl_agent.control.voice.import_module", side_effect=import_voice_module):
            backend = HermesVoiceBackend(Path("/unused"))
            callbacks = {
                "on_transcript": lambda _: None,
                "on_status": lambda _: None,
                "on_silent_limit": lambda: None,
                "on_stop_phrase": lambda _: None,
            }
            backend.start_voice(**callbacks)
            backend.speak("native reply")

        self.assertEqual(captured["silence_threshold"], 600)
        self.assertEqual(captured["silence_duration"], 1.0)
        self.assertTrue(callable(captured["on_transcript"]))
        self.assertTrue(callable(captured["on_status"]))
        self.assertEqual(spoken, ["native reply"])

    def test_hermes_backend_honors_native_profile_endpoint_overrides(self) -> None:
        config = ModuleType("hermes_cli.config")
        config.read_raw_config_readonly = lambda: {  # type: ignore[attr-defined]
            "voice": {"silence_threshold": 712, "silence_duration": 1.5}
        }
        voice = ModuleType("hermes_cli.voice")
        captured: dict[str, object] = {}
        voice.start_continuous = lambda **kwargs: captured.update(kwargs)  # type: ignore[attr-defined]
        voice.stop_continuous = lambda **kwargs: None  # type: ignore[attr-defined]
        voice.speak_text = lambda text: None  # type: ignore[attr-defined]

        def import_voice_module(name: str):
            return config if name == "hermes_cli.config" else voice

        with patch("jl_agent.control.voice.import_module", side_effect=import_voice_module):
            HermesVoiceBackend(Path("/unused")).start_voice(
                on_transcript=lambda _: None,
                on_status=lambda _: None,
                on_silent_limit=lambda: None,
                on_stop_phrase=lambda _: None,
            )

        self.assertEqual(captured["silence_threshold"], 712)
        self.assertEqual(captured["silence_duration"], 1.5)

    def test_hermes_turn_runner_passes_an_explicit_empty_toolset(self) -> None:
        captured = {}

        def fake_run(prompt, **kwargs):
            captured.update(prompt=prompt, **kwargs)
            return "safe reply", {"final_response": "safe reply"}

        package = ModuleType("hermes_cli")
        package.__path__ = []
        oneshot = ModuleType("hermes_cli.oneshot")
        oneshot._run_agent = fake_run  # type: ignore[attr-defined]
        with patch.dict("sys.modules", {"hermes_cli": package, "hermes_cli.oneshot": oneshot}):
            response = HermesTextOnlyTurnRunner(Path("/unused"))("hello")

        self.assertEqual(response, "safe reply")
        self.assertEqual(captured["toolsets"], [])
        self.assertFalse(captured["use_config_toolsets"])

    def test_model_cache_stays_inside_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model_root = Path(temporary) / "models"
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("HF_HOME", None)
                HermesVoiceBackend(Path(temporary) / "hermes", model_cache_root=model_root)
                self.assertEqual(os.environ["HF_HOME"], str(model_root / "huggingface"))


if __name__ == "__main__":
    unittest.main()
