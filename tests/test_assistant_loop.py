from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from jl_agent.control.approvals import OneTimeApprovalStore
from jl_agent.control.assistant_loop import AssistantAdmission
from jl_agent.control.audit import AuditLedger
from jl_agent.control.auth import FileCredentialProvider
from jl_agent.control.automation_management import (
    AutomationManager,
    AutomationRequestHandler,
)
from jl_agent.control.codec import decode_control_request
from jl_agent.control.consent import ConsentCoordinator
from jl_agent.control.control_plane import JLControlPlane
from jl_agent.control.execution import ExecutionGate
from jl_agent.control.execution_adapter import (
    HermesExecutionAdapter,
    HermesExecutionStatus,
    HermesRuntimeRequest,
    HermesRuntimeResult,
)
from jl_agent.control.health import ProbeOutcome
from jl_agent.control.hermes_projection import HermesProjection
from jl_agent.control.ipc import PROTOCOL_VERSION, IPCRequestEnvelope
from jl_agent.control.registry import HealthState
from jl_agent.control.request_state import SecureControlRequestHandler
from jl_agent.control.router import DeterministicModelRouter, RouterPolicy

ROOT = Path(__file__).resolve().parents[1]


class FakeRuntime:
    def __init__(self) -> None:
        self.requests: list[HermesRuntimeRequest] = []

    def run(self, request: HermesRuntimeRequest) -> HermesRuntimeResult:
        self.requests.append(request)
        return HermesRuntimeResult(HermesExecutionStatus.COMPLETED, "computer result")


class AssistantAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        runtime_root = Path(self.temporary.name) / "runtime"
        self.credentials = FileCredentialProvider(runtime_root / "ipc.credential")
        self.credential = self.credentials.load_or_create()
        self.approvals = OneTimeApprovalStore()
        self.manager = AutomationManager(
            upstream=ROOT / "upstream" / "hermes-agent",
            home=runtime_root / "automation",
            approvals=self.approvals,
        )
        self.automation_handler = AutomationRequestHandler(
            self.manager, scheduler_enabled=lambda: False
        )
        self.runtime = FakeRuntime()
        self.control_plane = JLControlPlane(
            projection=HermesProjection(ROOT / "upstream" / "hermes-agent"),
            router=DeterministicModelRouter(RouterPolicy(max_attempts=2)),
            trusted_probe_provider=lambda _: ProbeOutcome(
                HealthState.HEALTHY, "deterministic test readiness"
            ),
        )
        self.audit = AuditLedger(runtime_root / "audit.jsonl")
        self.gate = ExecutionGate(
            control_plane=self.control_plane,
            approvals=self.approvals,
            adapter=HermesExecutionAdapter(self.runtime),
            audit=self.audit,
        )
        self.turns: list[str] = []
        self.handler = SecureControlRequestHandler(
            credentials=self.credentials,
            approvals=self.approvals,
            control_plane=self.control_plane,
            request_decoder=decode_control_request,
            execution_gate=self.gate,
            consent_coordinator=ConsentCoordinator(),
            automation_handler=self.automation_handler,
        )
        self.assistant = AssistantAdmission(
            turn_runner=self._turn,
            control_handler=self.handler,
            automation_handler=self.automation_handler,
            clock=lambda: datetime(2026, 9, 16, 8, 0, tzinfo=UTC),
        )
        self.handler.assistant_handler = self.assistant.handle

    def tearDown(self) -> None:
        self.manager.close()
        self.temporary.cleanup()

    def _turn(self, text: str) -> str:
        self.turns.append(text)
        return f"conversation: {text}"

    def request(
        self,
        text: str,
        *,
        request_id: str = "assistant-1",
        credential: str | None = None,
        **fields: object,
    ):
        return self.handler(
            IPCRequestEnvelope(
                protocol_version=PROTOCOL_VERSION,
                request_id=request_id,
                caller_id="native-app",
                session_id="session-1",
                operation="assistant-request",
                payload={"text": text, **fields},
                credential=self.credential if credential is None else credential,
            )
        )

    def test_authentication_precedes_classification_and_dispatch(self) -> None:
        response = self.request("hello", credential="wrong")

        self.assertFalse(response.ok)
        self.assertEqual(response.error_code, "authentication_failed")
        self.assertEqual(self.turns, [])
        self.assertEqual(self.runtime.requests, [])

    def test_typed_and_voice_conversation_share_admission(self) -> None:
        response = self.request("hello jl", input_mode="typed", timezone="UTC")

        self.assertTrue(response.ok)
        self.assertEqual(response.result["state"], "conversation_completed")
        self.assertEqual(response.result["reply"], "conversation: hello jl")
        self.assertEqual(self.turns, ["hello jl"])

        voice_input = self.request(
            "hello jl", request_id="assistant-voice", input_mode="voice"
        )
        self.assertTrue(voice_input.ok)
        self.assertEqual(voice_input.result["state"], "conversation_completed")
        self.assertEqual(voice_input.result["reply"], "conversation: hello jl")
        self.assertEqual(self.turns, ["hello jl", "hello jl"])

    def test_voice_action_like_input_does_not_fall_through_to_conversation(
        self,
    ) -> None:
        response = self.request("click OK", input_mode="voice")

        self.assertTrue(response.ok)
        self.assertEqual(response.result["state"], "clarification_required")
        self.assertEqual(self.turns, [])
        self.assertEqual(self.runtime.requests, [])

    def test_malformed_or_oversized_input_fails_closed(self) -> None:
        malformed = self.request("hello", unexpected=True)
        oversized = self.request("x" * 16_385, request_id="oversized")
        invalid_mode = self.request("hello", input_mode=["voice"])

        self.assertFalse(malformed.ok)
        self.assertEqual(malformed.error_code, "malformed_payload")
        self.assertFalse(oversized.ok)
        self.assertEqual(oversized.error_code, "malformed_payload")
        self.assertFalse(invalid_mode.ok)
        self.assertEqual(invalid_mode.error_code, "malformed_payload")
        self.assertEqual(self.turns, [])

    def test_unsupported_action_does_not_fall_through_to_conversation(self) -> None:
        response = self.request("open Notes")

        self.assertTrue(response.ok)
        self.assertEqual(response.result["state"], "clarification_required")
        self.assertEqual(self.turns, [])
        self.assertEqual(self.runtime.requests, [])

    def test_relative_reminder_is_created_paused_through_existing_manager(self) -> None:
        response = self.request("remind me to hydrate in 10 minutes")

        self.assertTrue(response.ok)
        self.assertEqual(response.result["state"], "schedule_created_paused")
        job = response.result["job"]
        self.assertIsInstance(job, dict)
        assert isinstance(job, dict)
        self.assertEqual(job["prompt"], "hydrate")
        self.assertFalse(job["enabled"])
        self.assertEqual(job["state"], "paused")
        self.assertEqual(self.manager.list_jobs()["jobs"], [job])

    def test_ambiguous_reminder_requires_clarification(self) -> None:
        response = self.request("remind me to hydrate tomorrow at 9")

        self.assertTrue(response.ok)
        self.assertEqual(response.result["state"], "clarification_required")
        self.assertEqual(self.manager.list_jobs()["jobs"], [])
        self.assertEqual(self.turns, [])

    def test_simple_absolute_reminder_is_normalized_and_created_paused(self) -> None:
        response = self.request(
            "remind me to hydrate 2099-01-01 at 09:30", request_id="absolute"
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.result["state"], "schedule_created_paused")
        job = response.result["job"]
        self.assertIsInstance(job, dict)
        assert isinstance(job, dict)
        self.assertEqual(job["schedule_display"], "once at 2099-01-01 09:30")
        self.assertFalse(job["enabled"])

    def test_invalid_absolute_reminder_requires_clarification(self) -> None:
        response = self.request("remind me to hydrate 2026-02-30 at 09:30")

        self.assertTrue(response.ok)
        self.assertEqual(response.result["state"], "clarification_required")
        self.assertEqual(self.manager.list_jobs()["jobs"], [])

    def test_read_only_list_apps_delegates_to_existing_execution_gate(self) -> None:
        response = self.request("list apps", request_id="list-apps")

        self.assertTrue(response.ok)
        self.assertEqual(response.result["state"], "action_completed")
        self.assertEqual(response.result["action"], "list_apps")
        self.assertEqual(len(self.runtime.requests), 1)
        runtime_request = self.runtime.requests[0]
        self.assertEqual(runtime_request.enabled_toolsets, ("computer_use",))
        self.assertEqual(runtime_request.allowed_tool, "computer_use")
        self.assertEqual(
            json.loads(runtime_request.arguments_json), {"action": "list_apps"}
        )

    def test_read_only_list_windows_delegates_to_existing_execution_gate(self) -> None:
        response = self.request(
            "list windows", request_id="list-windows", foreground_app="TextEdit"
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.result["state"], "action_completed")
        self.assertEqual(response.result["action"], "list_windows")
        self.assertEqual(len(self.runtime.requests), 1)
        self.assertEqual(
            json.loads(self.runtime.requests[0].arguments_json),
            {"action": "list_windows"},
        )

    def test_capture_is_read_only_but_still_waits_for_existing_consent(self) -> None:
        response = self.request(
            "capture current screen",
            request_id="capture",
            foreground_app="com.apple.TextEdit",
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.result["state"], "awaiting_approval")
        self.assertIn("consent", response.result)
        self.assertEqual(self.runtime.requests, [])

    def test_mutating_computer_action_is_outside_first_slice(self) -> None:
        response = self.request("click OK", foreground_app="com.apple.TextEdit")

        self.assertTrue(response.ok)
        self.assertEqual(response.result["state"], "clarification_required")
        self.assertEqual(self.runtime.requests, [])
        self.assertEqual(self.turns, [])


if __name__ == "__main__":
    unittest.main()
