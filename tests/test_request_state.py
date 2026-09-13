from __future__ import annotations

import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from jl_agent.control.approvals import ApprovalState, OneTimeApprovalStore
from jl_agent.control.auth import FileCredentialProvider
from jl_agent.control.control_plane import ControlRequest, JLControlPlane
from jl_agent.control.hermes_projection import HermesProjection
from jl_agent.control.ipc import (
    PROTOCOL_VERSION,
    IPCRequestEnvelope,
    UnixSocketServer,
    send_request,
)
from jl_agent.control.permissions import ActionProposal
from jl_agent.control.registry import HealthState
from jl_agent.control.request_state import (
    IllegalRequestTransition,
    RequestLifecycle,
    RequestState,
    SecureControlRequestHandler,
)
from jl_agent.control.router import (
    CostClass,
    DeterministicModelRouter,
    ModelCandidate,
    RouteRequest,
    RouterPolicy,
    TaskCategory,
)

ROOT = Path(__file__).resolve().parents[1]
HERMES_ROOT = ROOT / "upstream" / "hermes-agent"
ROUTER_FIXTURE = ROOT / "config" / "model-router.example.yaml"


class RequestStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        runtime = Path(self.temporary.name) / "runtime"
        self.credentials = FileCredentialProvider(runtime / "ipc.credential")
        self.credential = self.credentials.load_or_create()
        self.approvals = OneTimeApprovalStore()
        self.control_plane = JLControlPlane(
            projection=HermesProjection(HERMES_ROOT),
            router=DeterministicModelRouter(RouterPolicy.from_file(ROUTER_FIXTURE)),
        )
        self.candidate = ModelCandidate(
            id="fake.local",
            provider="fake-provider",
            model="fake-model",
            abilities=frozenset({"text", "tool-calling"}),
            health=HealthState.HEALTHY,
            local=True,
            data_residency="device",
            cost_class=CostClass.FREE,
        )
        self.current_request = self.control_request(
            ActionProposal(
                action="read_file",
                normalized_arguments={"path": "docs/ARCHITECTURE.md"},
                requested_permissions=("local.read",),
                resolved_target=str(ROOT / "docs" / "ARCHITECTURE.md"),
                caller="native-app",
                session="session-1",
            )
        )
        self.decode_calls = 0

        def decode(_: IPCRequestEnvelope) -> ControlRequest:
            self.decode_calls += 1
            return self.current_request

        self.handler = SecureControlRequestHandler(
            credentials=self.credentials,
            approvals=self.approvals,
            control_plane=self.control_plane,
            request_decoder=decode,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def control_request(self, action: ActionProposal) -> ControlRequest:
        return ControlRequest(
            capability_id="core.hermes.files",
            action=action,
            route=RouteRequest(
                category=TaskCategory.SIMPLE,
                required_abilities=frozenset({"text", "tool-calling"}),
                local_only=True,
                off_device_allowed=False,
            ),
            candidates=(self.candidate,) if hasattr(self, "candidate") else (),
        )

    def envelope(
        self,
        *,
        credential: str | None = None,
        approval_id: str | None = None,
    ) -> IPCRequestEnvelope:
        return IPCRequestEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id="request-1",
            caller_id="native-app",
            session_id="session-1",
            operation="prepare",
            payload={"approval_id": approval_id},
            credential=self.credential if credential is None else credential,
        )

    def test_explicit_legal_transitions_reject_bypasses(self) -> None:
        lifecycle = RequestLifecycle("request-1")

        with self.assertRaises(IllegalRequestTransition):
            lifecycle.transition(RequestState.PREPARED)
        lifecycle.transition(RequestState.AUTHENTICATED)
        lifecycle.transition(RequestState.POLICY_CHECKED)
        lifecycle.transition(RequestState.APPROVED)
        lifecycle.transition(RequestState.PREPARED)
        with self.assertRaises(IllegalRequestTransition):
            lifecycle.transition(RequestState.FAILED)

    def test_ipc_authentication_cannot_be_bypassed(self) -> None:
        response = self.handler(self.envelope(credential="invalid"))

        self.assertFalse(response.ok)
        self.assertEqual(response.error_code, "authentication_failed")
        self.assertEqual(self.decode_calls, 0)

    def test_allow_policy_reaches_prepared_without_approval(self) -> None:
        response = self.handler(self.envelope())

        self.assertTrue(response.ok)
        assert response.result is not None
        self.assertEqual(response.result["state"], "prepared")
        self.assertEqual(
            response.result["history"],
            ["received", "authenticated", "policy_checked", "approved", "prepared"],
        )
        self.assertEqual(
            response.result["invocation"]["entrypoint_address"], "file"
        )

    def test_authenticated_ipc_reaches_only_an_inert_prepared_reference(self) -> None:
        endpoint = self.credentials.path.parent / "jl-agent.sock"
        with UnixSocketServer(endpoint, self.handler, timeout_seconds=0.2) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            response = send_request(endpoint, self.envelope())
        thread.join(timeout=1)

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["state"], "prepared")
        self.assertEqual(
            response["result"]["invocation"]["entrypoint_kind"], "hermes-tool"
        )

    def test_deny_policy_cannot_reach_prepared(self) -> None:
        self.current_request = self.control_request(
            ActionProposal(
                action="delete_file",
                normalized_arguments={"path": "/outside/file"},
                requested_permissions=("local.delete",),
                resolved_target="/outside/file",
                caller="native-app",
                session="session-1",
                unattended=True,
            )
        )

        response = self.handler(self.envelope())

        self.assertFalse(response.ok)
        self.assertEqual(response.error_code, "policy_denied")
        self.assertNotIn("prepared", response.error_message or "")

    def test_confirm_requires_and_consumes_the_exact_approval(self) -> None:
        self.current_request = self.control_request(
            ActionProposal(
                action="write_file",
                normalized_arguments={"path": "/outside/file", "content": "safe"},
                requested_permissions=("local.write.reversible",),
                resolved_target="/outside/file",
                caller="native-app",
                session="session-1",
                target_within_workspace=False,
            )
        )
        waiting = self.handler(self.envelope())
        assert waiting.result is not None
        fingerprint = waiting.result["approval_fingerprint"]
        self.assertEqual(waiting.result["state"], "awaiting_approval")

        issued = self.approvals.issue(
            binding_fingerprint=fingerprint,
            caller_id="native-app",
            session_id="session-1",
            ttl_seconds=30,
        )
        self.approvals.make_available(issued.approval_id)
        prepared = self.handler(self.envelope(approval_id=issued.approval_id))

        self.assertTrue(prepared.ok)
        assert prepared.result is not None
        self.assertEqual(prepared.result["state"], "prepared")
        self.assertEqual(
            self.approvals.get(issued.approval_id).state, ApprovalState.CONSUMED
        )

        replay = self.handler(self.envelope(approval_id=issued.approval_id))
        self.assertFalse(replay.ok)
        self.assertEqual(replay.error_code, "approval_denied")

    def test_approval_cannot_bypass_changed_policy_or_identity(self) -> None:
        confirmation = replace(
            self.current_request.action,
            action="write_file",
            requested_permissions=("local.write.reversible",),
            resolved_target="/outside/file",
            target_within_workspace=False,
        )
        confirmation_request = self.control_request(confirmation)
        decision = self.control_plane.check_policy(confirmation_request).permission
        issued = self.approvals.issue(
            binding_fingerprint=decision.binding_fingerprint,
            caller_id="native-app",
            session_id="session-1",
            ttl_seconds=30,
        )
        self.approvals.make_available(issued.approval_id)

        self.current_request = self.control_request(
            replace(
                confirmation,
                action="delete_file",
                requested_permissions=("local.delete",),
                unattended=True,
            )
        )
        denied = self.handler(self.envelope(approval_id=issued.approval_id))
        self.assertEqual(denied.error_code, "policy_denied")
        self.assertEqual(
            self.approvals.get(issued.approval_id).state, ApprovalState.AVAILABLE
        )

        self.current_request = confirmation_request
        wrong_identity = replace(self.envelope(approval_id=issued.approval_id),
                                 session_id="other-session")
        denied_identity = self.handler(wrong_identity)
        self.assertEqual(denied_identity.error_code, "identity_mismatch")
        self.assertEqual(
            self.approvals.get(issued.approval_id).state, ApprovalState.AVAILABLE
        )


if __name__ == "__main__":
    unittest.main()
