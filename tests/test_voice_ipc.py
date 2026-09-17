from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jl_agent.control.approvals import OneTimeApprovalStore
from jl_agent.control.assistant_loop import AssistantAdmission
from jl_agent.control.auth import FileCredentialProvider
from jl_agent.control.control_plane import JLControlPlane
from jl_agent.control.hermes_projection import HermesProjection
from jl_agent.control.ipc import PROTOCOL_VERSION, IPCRequestEnvelope
from jl_agent.control.request_state import SecureControlRequestHandler
from jl_agent.control.router import DeterministicModelRouter, RouterPolicy
from jl_agent.control.voice import VoiceCoordinator

ROOT = Path(__file__).resolve().parents[1]


class FakeBackend:
    def __init__(self) -> None:
        self.voice_callback = None
        self.wake_callback = None

    def requirements(self) -> dict[str, object]:
        return {
            "voice": {"available": True, "details": "ready"},
            "wake": {"available": True, "phrase": "hey hermes", "hint": ""},
        }

    def start_voice(self, *, on_transcript, on_status, on_stop_phrase) -> None:
        self.voice_callback = on_transcript
        on_status("listening")

    def stop_voice(self) -> None:
        pass

    def start_wake(self, *, on_wake, phrase: str) -> None:
        self.wake_callback = on_wake

    def stop_wake(self) -> None:
        pass

    def pause_wake(self) -> None:
        pass

    def resume_wake(self) -> None:
        pass

    def speak(self, text: str) -> None:
        pass


class VoiceIPCTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        provider = FileCredentialProvider(
            Path(self.temporary.name) / "runtime" / "ipc.credential"
        )
        self.credential = provider.load_or_create()
        self.handler = SecureControlRequestHandler(
            credentials=provider,
            approvals=OneTimeApprovalStore(),
            control_plane=JLControlPlane(
                projection=HermesProjection(ROOT / "upstream" / "hermes-agent"),
                router=DeterministicModelRouter(RouterPolicy(max_attempts=2)),
            ),
            request_decoder=lambda _: (_ for _ in ()).throw(
                AssertionError("voice operations must not decode actions")
            ),
        )
        assistant = AssistantAdmission(
            turn_runner=lambda text: f"reply: {text}",
            control_handler=self.handler,
            automation_handler=lambda envelope: self.handler(envelope),
        )
        self.handler.assistant_handler = assistant.handle
        self.backend = FakeBackend()
        self.voice = VoiceCoordinator(
            backend=self.backend,
            assistant_handler=self.handler,
            credential=self.credential,
            enabled=True,
            activation_approved=True,
            run_async=lambda task: task(),
        )
        self.handler.voice_handler = self.voice.handle

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, operation: str, payload=None, credential="valid", session="s1"):
        return self.handler(
            IPCRequestEnvelope(
                protocol_version=PROTOCOL_VERSION,
                request_id="r1",
                caller_id="native-app",
                session_id=session,
                operation=operation,
                payload=payload or {},
                credential=self.credential if credential == "valid" else credential,
            )
        )

    def test_voice_operations_are_authenticated_before_dispatch(self) -> None:
        response = self.request("voice-status", credential=None)
        self.assertFalse(response.ok)
        self.assertEqual(response.error_code, "authentication_failed")

    def test_voice_start_and_events_use_authenticated_identity(self) -> None:
        started = self.request("voice-start")
        self.assertFalse(started.ok)
        self.assertEqual(started.error_code, "voice_requires_wake_phrase")

        started = self.request("wake-start")
        self.assertTrue(started.ok)
        self.assertTrue(started.result["wake"]["active"])

        denied = self.request("voice-events", session="s2")
        self.assertFalse(denied.ok)
        self.assertEqual(denied.error_code, "voice_session_mismatch")

    def test_transcript_uses_authenticated_shared_assistant_admission(self) -> None:
        started = self.request("wake-start")
        self.assertTrue(started.ok)
        assert self.backend.wake_callback is not None
        self.backend.wake_callback()
        assert self.backend.voice_callback is not None

        self.backend.voice_callback("hello jl")

        events = self.request("voice-events").result["events"]
        self.assertEqual(events[-1]["kind"], "reply")
        self.assertEqual(events[-1]["text"], "reply: hello jl")

    def test_unknown_voice_payload_fields_fail_closed(self) -> None:
        response = self.request("voice-start", {"surprise": True})
        self.assertFalse(response.ok)
        self.assertEqual(response.error_code, "malformed_payload")

    def test_wake_phrase_operations_are_authenticated_and_test_gated(self) -> None:
        started = self.request("wake-test-start", {"phrase": "hello jl"})
        self.assertTrue(started.ok)
        denied = self.request("wake-phrase-set", {"phrase": "hello jl"})
        self.assertFalse(denied.ok)
        self.assertEqual(denied.error_code, "wake_phrase_not_tested")


if __name__ == "__main__":
    unittest.main()
