from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path

from jl_agent.control.approvals import OneTimeApprovalStore
from jl_agent.control.audit import AuditLedger
from jl_agent.control.auth import FileCredentialProvider
from jl_agent.control.control_plane import JLControlPlane
from jl_agent.control.hermes_projection import HermesProjection
from jl_agent.control.ipc import PROTOCOL_VERSION, IPCRequestEnvelope
from jl_agent.control.request_state import SecureControlRequestHandler
from jl_agent.control.router import DeterministicModelRouter, RouterPolicy

ROOT = Path(__file__).resolve().parents[1]


class NativeClientSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        runtime = Path(self.temporary.name) / "runtime"
        self.credentials = FileCredentialProvider(runtime / "ipc.credential")
        self.credential = self.credentials.load_or_create()
        self.audit = AuditLedger(runtime / "audit.jsonl")
        self.handler = SecureControlRequestHandler(
            credentials=self.credentials,
            approvals=OneTimeApprovalStore(),
            control_plane=JLControlPlane(
                projection=HermesProjection(ROOT / "upstream" / "hermes-agent"),
                router=DeterministicModelRouter(RouterPolicy(max_attempts=2)),
            ),
            request_decoder=lambda _: (_ for _ in ()).throw(
                AssertionError("status/activity must not decode an action")
            ),
            status_provider=lambda: {
                "ready": True,
                "state": "ready",
                "protocol_version": 1,
                "transport": "AF_UNIX",
                "consent_available": True,
            },
            activity_reader=self.audit.safe_activity,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def envelope(
        self,
        operation: str,
        payload: dict[str, object],
        *,
        credential: str | None,
    ) -> IPCRequestEnvelope:
        return IPCRequestEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id="request-1",
            caller_id="native-app",
            session_id="session-1",
            operation=operation,
            payload=payload,
            credential=credential,
        )

    def test_status_and_activity_require_authentication(self) -> None:
        for operation in ("status", "activity"):
            missing = self.handler(
                self.envelope(operation, {}, credential=None)
            )
            wrong = self.handler(
                self.envelope(operation, {}, credential="wrong")
            )
            self.assertEqual(missing.error_code, "authentication_failed")
            self.assertEqual(wrong.error_code, "authentication_failed")

    def test_status_and_safe_activity_are_bounded_structured_data(self) -> None:
        self.audit.record(
            "execution_denied",
            request_id="request-1",
            caller_id="native-app",
            session_id="session-1",
            capability_id="core.hermes.files",
            action_class=("destructive",),
            policy_decision="requires-confirmation",
        )
        status = self.handler(
            self.envelope("status", {}, credential=self.credential)
        )
        activity = self.handler(
            self.envelope("activity", {"limit": 1}, credential=self.credential)
        )

        self.assertTrue(status.ok)
        self.assertEqual(status.result["state"], "ready")  # type: ignore[index]
        self.assertTrue(activity.ok)
        events = activity.result["events"]  # type: ignore[index]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["execution_status"], "denied")

        invalid = self.handler(
            self.envelope("activity", {"limit": 101}, credential=self.credential)
        )
        self.assertEqual(invalid.error_code, "activity_unavailable")

    def test_native_client_has_no_direct_hermes_execution_surface(self) -> None:
        native_root = ROOT / "macos-app" / "Sources"
        forbidden = (
            "invoke_tool",
            "run_agent",
            "upstream/hermes-agent",
            "CGEvent",
            "AXUIElement",
            "CGWindowListCreateImage",
            "screencapture",
        )
        for source in native_root.rglob("*.swift"):
            if "JLAgentNativeTests" in source.parts:
                continue
            text = source.read_text(encoding="utf-8")
            for marker in forbidden:
                self.assertNotIn(marker, text, f"{source} contains {marker}")

    def test_voice_runtime_has_stable_least_privilege_signing_contract(self) -> None:
        voice_root = ROOT / "macos-app" / "VoiceRuntime"
        with (voice_root / "Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        with (voice_root / "JLVoiceRuntime.entitlements").open("rb") as stream:
            entitlements = plistlib.load(stream)

        self.assertEqual(info["CFBundleIdentifier"], "com.jlagent.voice-runtime")
        self.assertEqual(info["CFBundleExecutable"], "JLVoiceRuntime")
        self.assertTrue(info["NSMicrophoneUsageDescription"])
        self.assertEqual(
            entitlements,
            {"com.apple.security.device.audio-input": True},
        )

        build_script = (
            ROOT / "macos-app" / "Scripts" / "build-voice-runtime.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("JL_VOICE_CODE_SIGN_IDENTITY", build_script)
        self.assertIn("Apple Development: ", build_script)
        self.assertIn("Developer ID Application: ", build_script)
        self.assertIn("--options runtime", build_script)
        self.assertIn("--deep --strict", build_script)
        self.assertIn("com.apple.security.device.audio-input", build_script)
        self.assertIn("codesign -dr -", build_script)
        self.assertNotIn("signing_identity:--", build_script)

        host_source = (
            ROOT / "macos-app" / "Sources" / "JLVoiceRuntime" / "main.swift"
        ).read_text(encoding="utf-8")
        for live_audio_marker in (
            "AVAudioEngine", "AVCaptureDevice", "AudioQueue", "sounddevice"
        ):
            self.assertNotIn(live_audio_marker, host_source)

    def test_voice_keeps_manual_fallback_and_uses_sherpa_candidate(self) -> None:
        app_root = ROOT / "macos-app" / "Sources" / "JLAgentApp"
        view_model = (app_root / "AgentViewModel.swift").read_text(encoding="utf-8")
        content = (app_root / "ContentView.swift").read_text(encoding="utf-8")

        self.assertIn('wakePhraseDraft = "HEY J L"', view_model)
        self.assertIn('Button("Call JL")', content)


if __name__ == "__main__":
    unittest.main()
