from __future__ import annotations

import unittest
from types import ModuleType
from unittest.mock import patch

from jl_agent.control.voice import (
    HermesTextOnlyTurnRunner,
    VoiceCoordinator,
    VoiceError,
)


class FakeHermesVoiceBackend:
    def __init__(self) -> None:
        self.voice_callback = None
        self.wake_callback = None
        self.spoken: list[str] = []
        self.voice_starts = 0
        self.wake_starts = 0

    def requirements(self) -> dict[str, object]:
        return {
            "voice": {"available": True, "details": "fake voice ready"},
            "wake": {
                "available": True,
                "phrase": "hey hermes",
                "hint": "",
            },
        }

    def start_voice(self, *, on_transcript, on_status, on_stop_phrase) -> None:
        self.voice_starts += 1
        self.voice_callback = on_transcript
        on_status("listening")

    def stop_voice(self) -> None:
        return

    def start_wake(self, *, on_wake) -> None:
        self.wake_starts += 1
        self.wake_callback = on_wake

    def stop_wake(self) -> None:
        return

    def pause_wake(self) -> None:
        return

    def resume_wake(self) -> None:
        return

    def speak(self, text: str) -> None:
        self.spoken.append(text)


class VoiceCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeHermesVoiceBackend()
        self.prompts: list[str] = []

        def run_turn(prompt: str) -> str:
            self.prompts.append(prompt)
            return f"reply: {prompt}"

        self.voice = VoiceCoordinator(
            backend=self.backend,
            turn_runner=run_turn,
            enabled=True,
            activation_approved=True,
            run_async=lambda task: task(),
        )

    def test_start_requires_both_feature_and_hardware_approval(self) -> None:
        disabled = VoiceCoordinator(
            backend=self.backend,
            turn_runner=lambda _: "",
            enabled=False,
            activation_approved=False,
        )
        with self.assertRaisesRegex(VoiceError, "voice_disabled"):
            disabled.start_voice("caller", "session")

        gated = VoiceCoordinator(
            backend=self.backend,
            turn_runner=lambda _: "",
            enabled=True,
            activation_approved=False,
        )
        with self.assertRaisesRegex(VoiceError, "voice_activation_not_approved"):
            gated.start_voice("caller", "session")
        self.assertEqual(self.backend.voice_starts, 0)

    def test_transcript_runs_a_text_only_turn_and_speaks_reply(self) -> None:
        self.voice.start_voice("caller", "session")
        assert self.backend.voice_callback is not None
        self.backend.voice_callback("hello")

        self.assertEqual(self.prompts, ["hello"])
        self.assertEqual(self.backend.spoken, ["reply: hello"])
        events = self.voice.events("caller", "session", after=0, limit=20)
        self.assertEqual(
            [event["kind"] for event in events],
            ["voice_status", "transcript", "reply"],
        )
        self.assertTrue(all("audio" not in event for event in events))

    def test_session_binding_blocks_cross_session_control_and_content(self) -> None:
        self.voice.start_voice("caller", "session")
        assert self.backend.voice_callback is not None
        self.backend.voice_callback("private transcript")

        with self.assertRaisesRegex(VoiceError, "voice_session_mismatch"):
            self.voice.stop_voice("caller", "other-session")
        with self.assertRaisesRegex(VoiceError, "voice_session_mismatch"):
            self.voice.events("caller", "other-session", after=0, limit=20)

    def test_wake_reuses_one_owner_and_arms_voice_on_detection(self) -> None:
        self.voice.start_wake("caller", "session")
        assert self.backend.wake_callback is not None
        self.backend.wake_callback()

        self.assertEqual(self.backend.wake_starts, 1)
        self.assertEqual(self.backend.voice_starts, 1)
        status = self.voice.status("caller", "session")
        self.assertTrue(status["wake"]["active"])
        self.assertTrue(status["voice"]["active"])
        self.assertFalse(status["tool_execution_enabled"])

    def test_event_query_is_strictly_bounded(self) -> None:
        with self.assertRaisesRegex(VoiceError, "malformed_payload"):
            self.voice.events("caller", "session", after=-1, limit=20)
        with self.assertRaisesRegex(VoiceError, "malformed_payload"):
            self.voice.events("caller", "session", after=0, limit=101)

    def test_hermes_turn_runner_passes_an_explicit_empty_toolset(self) -> None:
        captured = {}

        def fake_run(prompt, **kwargs):
            captured.update(prompt=prompt, **kwargs)
            return "safe reply", {"final_response": "safe reply"}

        package = ModuleType("hermes_cli")
        package.__path__ = []
        oneshot = ModuleType("hermes_cli.oneshot")
        oneshot._run_agent = fake_run  # type: ignore[attr-defined]
        with patch.dict(
            "sys.modules",
            {"hermes_cli": package, "hermes_cli.oneshot": oneshot},
        ):
            response = HermesTextOnlyTurnRunner(
                __import__("pathlib").Path(__file__).resolve().parents[1]
                / "upstream"
                / "hermes-agent"
            )("hello")

        self.assertEqual(response, "safe reply")
        self.assertEqual(captured["toolsets"], [])
        self.assertFalse(captured["use_config_toolsets"])


if __name__ == "__main__":
    unittest.main()
