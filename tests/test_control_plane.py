from __future__ import annotations

import unittest
from pathlib import Path

from jl_agent.control.control_plane import ControlRequest, JLControlPlane
from jl_agent.control.hermes_projection import HERMES_REVISION, HermesProjection
from jl_agent.control.permissions import (
    ActionProposal,
    DecisionOutcome,
)
from jl_agent.control.registry import HealthState
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


class JLControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.control_plane = JLControlPlane(
            projection=HermesProjection(HERMES_ROOT),
            router=DeterministicModelRouter(
                RouterPolicy.from_file(ROUTER_FIXTURE)
            ),
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

    def test_read_only_request_reaches_a_hermes_reference_end_to_end(self) -> None:
        result = self.control_plane.prepare(
            ControlRequest(
                capability_id="core.hermes.files",
                action=ActionProposal(
                    action="read_file",
                    normalized_arguments={"path": "docs/ARCHITECTURE.md"},
                    requested_permissions=("local.read",),
                    resolved_target=str(ROOT / "docs" / "ARCHITECTURE.md"),
                    session="integration-test",
                ),
                route=RouteRequest(
                    category=TaskCategory.SIMPLE,
                    required_abilities=frozenset({"text", "tool-calling"}),
                    local_only=True,
                    off_device_allowed=False,
                ),
                candidates=(self.candidate,),
            )
        )

        self.assertEqual(result.identity.revision, HERMES_REVISION)
        self.assertEqual(result.capability.health.state, HealthState.HEALTHY)
        self.assertEqual(result.permission.outcome, DecisionOutcome.MAY_PROCEED)
        self.assertIsNotNone(result.route)
        self.assertIsNotNone(result.invocation)
        assert result.invocation is not None
        self.assertEqual(result.invocation.entrypoint_kind, "hermes-tool")
        self.assertEqual(result.invocation.entrypoint_address, "file")
        self.assertEqual(result.invocation.provider, "fake-provider")

    def test_confirmation_stops_before_model_routing_or_invocation(self) -> None:
        result = self.control_plane.prepare(
            ControlRequest(
                capability_id="core.hermes.files",
                action=ActionProposal(
                    action="write_file",
                    normalized_arguments={"path": "/outside/file"},
                    requested_permissions=("local.write.reversible",),
                    resolved_target="/outside/file",
                    target_within_workspace=False,
                ),
                route=RouteRequest(
                    category=TaskCategory.SIMPLE,
                    required_abilities=frozenset({"text"}),
                ),
                candidates=(),
            )
        )

        self.assertEqual(
            result.permission.outcome, DecisionOutcome.REQUIRES_CONFIRMATION
        )
        self.assertIsNone(result.route)
        self.assertIsNone(result.invocation)


if __name__ == "__main__":
    unittest.main()
