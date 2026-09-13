from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from jl_agent.control.approvals import OneTimeApprovalStore
from jl_agent.control.audit import AuditLedger
from jl_agent.control.auth import FileCredentialProvider
from jl_agent.control.control_plane import ControlRequest, JLControlPlane
from jl_agent.control.execution import ExecutionGate
from jl_agent.control.execution_adapter import (
    ExecutionErrorCategory,
    HermesExecutionAdapter,
    HermesExecutionStatus,
    HermesRuntimeRequest,
    HermesRuntimeResult,
)
from jl_agent.control.hermes_projection import HermesProjection, HermesProjectionResult
from jl_agent.control.ipc import PROTOCOL_VERSION, IPCRequestEnvelope
from jl_agent.control.permissions import ActionProposal
from jl_agent.control.registry import CapabilityRegistry, HealthState
from jl_agent.control.request_state import SecureControlRequestHandler
from jl_agent.control.router import (
    CostClass,
    DeterministicModelRouter,
    ModelCandidate,
    RouteRequest,
    RouterPolicy,
    TaskCategory,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeRuntime:
    def __init__(self) -> None:
        self.requests: list[HermesRuntimeRequest] = []
        self.result = HermesRuntimeResult(HermesExecutionStatus.COMPLETED, "safe")

    def run(self, request: HermesRuntimeRequest) -> HermesRuntimeResult:
        self.requests.append(request)
        return self.result


class MutableProjection:
    def __init__(self) -> None:
        self.base = HermesProjection(ROOT / "upstream" / "hermes-agent")
        self.enabled = True
        self.version: str | None = None

    def project(self) -> HermesProjectionResult:
        projected = self.base.project()
        capabilities = tuple(
            replace(
                item,
                enabled=self.enabled
                if item.id == "core.hermes.files"
                else item.enabled,
                version=(self.version or item.version)
                if item.id == "core.hermes.files"
                else item.version,
            )
            for item in projected.registry.capabilities
        )
        return replace(projected, registry=CapabilityRegistry(1, capabilities))


class ExecutionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        runtime_root = Path(self.temporary.name) / "runtime"
        self.credentials = FileCredentialProvider(runtime_root / "ipc.credential")
        self.credential = self.credentials.load_or_create()
        self.approvals = OneTimeApprovalStore()
        self.projection = MutableProjection()
        self.control_plane = JLControlPlane(
            projection=self.projection,  # type: ignore[arg-type]
            router=DeterministicModelRouter(RouterPolicy(max_attempts=2)),
        )
        self.runtime = FakeRuntime()
        self.audit = AuditLedger(runtime_root / "audit.jsonl")
        self.gate = ExecutionGate(
            control_plane=self.control_plane,
            approvals=self.approvals,
            adapter=HermesExecutionAdapter(self.runtime),
            audit=self.audit,
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
        self.current_request = self.request()

        def decode(_: IPCRequestEnvelope) -> ControlRequest:
            return self.current_request

        self.handler = SecureControlRequestHandler(
            credentials=self.credentials,
            approvals=self.approvals,
            control_plane=self.control_plane,
            request_decoder=decode,
            execution_gate=self.gate,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, action: ActionProposal | None = None) -> ControlRequest:
        return ControlRequest(
            capability_id="core.hermes.files",
            action=action
            or ActionProposal(
                action="read_file",
                normalized_arguments={"path": "docs/ARCHITECTURE.md"},
                requested_permissions=("local.read",),
                resolved_target=str(ROOT / "docs" / "ARCHITECTURE.md"),
                caller="native-app",
                session="session-1",
            ),
            route=RouteRequest(
                category=TaskCategory.SIMPLE,
                required_abilities=frozenset({"text", "tool-calling"}),
                local_only=True,
                off_device_allowed=False,
            ),
            candidates=(self.candidate,),
        )

    def envelope(
        self,
        operation: str,
        *,
        request_id: str = "request-1",
        credential: str | None = None,
        approval_id: str | None = None,
        caller_id: str = "native-app",
        session_id: str = "session-1",
    ) -> IPCRequestEnvelope:
        return IPCRequestEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id=request_id,
            caller_id=caller_id,
            session_id=session_id,
            operation=operation,
            payload={"approval_id": approval_id},
            credential=self.credential if credential is None else credential,
        )

    def test_safe_path_reaches_fake_hermes_once_with_expected_events(self) -> None:
        prepared = self.handler(self.envelope("prepare"))
        executed = self.handler(self.envelope("execute"))

        self.assertTrue(prepared.ok)
        self.assertTrue(executed.ok)
        self.assertEqual(executed.result["state"], "completed")  # type: ignore[index]
        self.assertEqual(executed.result["request_id"], "request-1")  # type: ignore[index]
        self.assertEqual(executed.result["session_id"], "session-1")  # type: ignore[index]
        self.assertEqual(len(self.runtime.requests), 1)
        self.assertEqual(
            [event["event"] for event in self.audit.read()],
            ["execution_prepared", "execution_started", "execution_completed"],
        )

        duplicate = self.handler(self.envelope("execute"))
        self.assertFalse(duplicate.ok)
        self.assertEqual(
            duplicate.error_code, ExecutionErrorCategory.DUPLICATE_EXECUTION.value
        )
        self.assertEqual(len(self.runtime.requests), 1)
        self.assertEqual(self.audit.read()[-1]["event"], "execution_denied")

    def test_authentication_and_identity_fail_before_execution(self) -> None:
        unauthenticated = self.handler(
            self.envelope("prepare", credential="wrong-credential")
        )
        self.assertEqual(unauthenticated.error_code, "authentication_failed")

        mismatch = self.handler(self.envelope("prepare", session_id="changed-session"))
        self.assertEqual(mismatch.error_code, "identity_mismatch")
        self.assertEqual(self.runtime.requests, [])
        self.assertEqual(
            [event["error_category"] for event in self.audit.read()],
            ["authentication_failed", "identity_mismatch"],
        )

    def test_changed_arguments_and_capability_version_fail_closed(self) -> None:
        self.assertTrue(self.handler(self.envelope("prepare")).ok)
        self.current_request = replace(
            self.current_request,
            action=replace(
                self.current_request.action,
                normalized_arguments={"path": "docs/SECURITY_MODEL.md"},
            ),
        )
        changed = self.handler(self.envelope("execute"))
        self.assertEqual(
            changed.error_code, ExecutionErrorCategory.STALE_PREPARATION.value
        )
        self.assertEqual(self.runtime.requests, [])

        self.current_request = self.request()
        self.assertTrue(
            self.handler(self.envelope("prepare", request_id="request-2")).ok
        )
        self.projection.version = "changed-version"
        version = self.handler(self.envelope("execute", request_id="request-2"))
        self.assertEqual(
            version.error_code, ExecutionErrorCategory.STALE_PREPARATION.value
        )
        self.assertEqual(self.runtime.requests, [])

    def test_disabled_unhealthy_denied_and_route_changes_fail_closed(self) -> None:
        self.assertTrue(self.handler(self.envelope("prepare")).ok)
        self.projection.enabled = False
        disabled = self.handler(self.envelope("execute"))
        self.assertEqual(
            disabled.error_code, ExecutionErrorCategory.POLICY_DENIED.value
        )

        self.projection.enabled = True
        self.current_request = self.request()
        self.assertTrue(
            self.handler(self.envelope("prepare", request_id="request-2")).ok
        )
        self.current_request = replace(
            self.current_request,
            candidates=(replace(self.candidate, model="changed-model"),),
        )
        rerouted = self.handler(self.envelope("execute", request_id="request-2"))
        self.assertEqual(
            rerouted.error_code, ExecutionErrorCategory.ROUTING_MISMATCH.value
        )

        self.current_request = replace(
            self.request(),
            probe=None,
            dependency_states={"core.hermes.agent": HealthState.UNAVAILABLE},
        )
        unhealthy = self.handler(self.envelope("prepare", request_id="request-3"))
        self.assertEqual(unhealthy.error_code, "policy_denied")
        self.assertEqual(self.runtime.requests, [])

    def test_changed_action_and_missing_preparation_fail_closed(self) -> None:
        missing = self.handler(self.envelope("execute", request_id="missing"))
        self.assertEqual(
            missing.error_code, ExecutionErrorCategory.STALE_PREPARATION.value
        )
        self.assertEqual(
            self.audit.read()[-1]["error_category"],
            ExecutionErrorCategory.STALE_PREPARATION.value,
        )

        self.assertTrue(self.handler(self.envelope("prepare")).ok)
        self.gate._prepared["request-1"].expires_at = 0
        stale = self.handler(self.envelope("execute"))
        self.assertEqual(
            stale.error_code, ExecutionErrorCategory.STALE_PREPARATION.value
        )

        self.current_request = self.request()
        self.assertTrue(
            self.handler(self.envelope("prepare", request_id="request-2")).ok
        )
        self.current_request = replace(
            self.current_request,
            action=replace(self.current_request.action, action="search_files"),
        )
        changed = self.handler(self.envelope("execute", request_id="request-2"))
        self.assertEqual(
            changed.error_code, ExecutionErrorCategory.STALE_PREPARATION.value
        )
        self.assertEqual(self.runtime.requests, [])

    def test_confirmation_requires_one_consumed_exact_approval(self) -> None:
        self.current_request = self.request(
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
        waiting = self.handler(self.envelope("prepare"))
        self.assertTrue(waiting.ok)
        self.assertEqual(waiting.result["state"], "awaiting_approval")  # type: ignore[index]
        fingerprint = waiting.result["approval_fingerprint"]  # type: ignore[index]
        issued = self.approvals.issue(
            binding_fingerprint=fingerprint,
            caller_id="native-app",
            session_id="session-1",
            ttl_seconds=30,
        )
        self.approvals.make_available(issued.approval_id)

        prepared = self.handler(
            self.envelope("prepare", approval_id=issued.approval_id)
        )
        self.assertTrue(prepared.ok)
        self.assertEqual(self.approvals.get(issued.approval_id).state.value, "consumed")
        ledger_text = self.audit.path.read_text(encoding="utf-8")
        self.assertNotIn(issued.approval_id, ledger_text)
        self.assertNotEqual(self.audit.read()[-1]["approval_id_reference"], "")

        replay = self.handler(
            self.envelope(
                "prepare",
                request_id="request-2",
                approval_id=issued.approval_id,
            )
        )
        self.assertEqual(replay.error_code, "approval_denied")
        self.assertTrue(self.handler(self.envelope("execute")).ok)
        self.assertEqual(len(self.runtime.requests), 1)

    def test_fresh_upstream_and_runtime_denials_propagate(self) -> None:
        self.assertTrue(self.handler(self.envelope("prepare")).ok)
        self.current_request = replace(
            self.current_request,
            action=replace(self.current_request.action, upstream_denied=True),
        )
        upstream = self.handler(self.envelope("execute"))
        self.assertEqual(
            upstream.error_code, ExecutionErrorCategory.POLICY_DENIED.value
        )
        self.assertEqual(self.runtime.requests, [])

        self.current_request = self.request()
        self.runtime.result = HermesRuntimeResult(
            HermesExecutionStatus.DENIED,
            error_category=ExecutionErrorCategory.UPSTREAM_DENIED,
        )
        self.assertTrue(
            self.handler(self.envelope("prepare", request_id="request-2")).ok
        )
        denied = self.handler(self.envelope("execute", request_id="request-2"))
        self.assertEqual(
            denied.error_code, ExecutionErrorCategory.UPSTREAM_DENIED.value
        )
        self.assertEqual(self.audit.read()[-1]["event"], "execution_denied")

    def test_runtime_failure_is_terminal_and_audited(self) -> None:
        self.runtime.result = HermesRuntimeResult(
            HermesExecutionStatus.FAILED,
            error_category=ExecutionErrorCategory.RUNTIME_ERROR,
        )
        self.assertTrue(self.handler(self.envelope("prepare")).ok)
        failed = self.handler(self.envelope("execute"))
        self.assertEqual(failed.error_code, ExecutionErrorCategory.RUNTIME_ERROR.value)
        self.assertEqual(self.audit.read()[-1]["event"], "execution_failed")

        repeated = self.handler(self.envelope("execute"))
        self.assertEqual(
            repeated.error_code, ExecutionErrorCategory.DUPLICATE_EXECUTION.value
        )


if __name__ == "__main__":
    unittest.main()
