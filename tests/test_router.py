from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from jl_agent.control.registry import HealthState
from jl_agent.control.router import (
    CostClass,
    DeterministicModelRouter,
    ModelCandidate,
    RouteRequest,
    RouterPolicy,
    RoutingError,
    TaskCategory,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "config" / "model-router.example.yaml"


class DeterministicModelRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = RouterPolicy.from_file(FIXTURE)
        self.router = DeterministicModelRouter(self.policy)
        self.local = ModelCandidate(
            id="local.small",
            provider="fake-local",
            model="small",
            abilities=frozenset({"text", "tool-calling"}),
            health=HealthState.HEALTHY,
            local=True,
            data_residency="device",
            cost_class=CostClass.FREE,
            latency_ms=100,
            quality=60,
            reliability=85,
            context_window=32_000,
        )
        self.cloud = ModelCandidate(
            id="cloud.strong",
            provider="fake-cloud",
            model="strong",
            abilities=frozenset(
                {"text", "tool-calling", "vision", "reasoning", "citations"}
            ),
            health=HealthState.HEALTHY,
            local=False,
            data_residency="us",
            cost_class=CostClass.MEDIUM,
            latency_ms=300,
            quality=95,
            reliability=95,
            context_window=128_000,
        )

    def request(self, **changes: object) -> RouteRequest:
        values: dict[str, object] = {
            "category": TaskCategory.SIMPLE,
            "required_abilities": frozenset({"text", "tool-calling"}),
        }
        values.update(changes)
        return RouteRequest(**values)  # type: ignore[arg-type]

    def test_example_policy_loads_and_simple_route_prefers_local(self) -> None:
        decision = self.router.route(self.request(), (self.cloud, self.local))

        self.assertTrue(self.policy.prefer_local)
        self.assertEqual(decision.selected.id, "local.small")
        self.assertEqual(decision.fallback_chain, ())

    def test_coding_route_prefers_quality_when_privacy_allows(self) -> None:
        decision = self.router.route(
            self.request(category=TaskCategory.CODING),
            (self.local, self.cloud),
        )

        self.assertEqual(decision.selected.id, "cloud.strong")
        self.assertEqual(decision.fallback_chain[0].id, "local.small")

    def test_locality_privacy_provider_and_cost_are_hard_filters(self) -> None:
        request = self.request(
            local_only=True,
            off_device_allowed=False,
            allowed_providers=frozenset({"fake-local"}),
            maximum_cost=CostClass.LOW,
        )

        decision = self.router.route(request, (self.cloud, self.local))

        self.assertEqual(decision.selected.id, "local.small")
        self.assertEqual(decision.rejected_candidates[0].candidate_id, "cloud.strong")

    def test_health_and_required_abilities_control_availability(self) -> None:
        broken = replace(self.cloud, id="cloud.broken", health=HealthState.UNAVAILABLE)
        request = self.request(required_abilities=frozenset({"vision"}))

        with self.assertRaises(RoutingError) as caught:
            self.router.route(request, (self.local, broken))

        rejected = {item.candidate_id for item in caught.exception.rejected_candidates}
        self.assertEqual(rejected, {"local.small", "cloud.broken"})

    def test_explicit_candidate_is_honored_only_if_policy_compliant(self) -> None:
        explicit = self.router.route(
            self.request(explicit_candidate_id="cloud.strong"),
            (self.local, self.cloud),
        )
        self.assertEqual(explicit.selected.id, "cloud.strong")

        with self.assertRaisesRegex(RoutingError, "explicit model"):
            self.router.route(
                self.request(
                    explicit_candidate_id="cloud.strong",
                    local_only=True,
                ),
                (self.local, self.cloud),
            )

    def test_fallback_is_bounded_and_deterministic(self) -> None:
        second_local = replace(
            self.local,
            id="local.second",
            model="second",
            latency_ms=200,
        )
        decision = self.router.route(
            self.request(), (self.cloud, second_local, self.local)
        )

        self.assertEqual(
            [candidate.id for candidate in decision.ordered_chain],
            ["local.small", "local.second"],
        )
        self.assertLessEqual(len(decision.ordered_chain), self.policy.max_attempts)

    def test_high_cost_route_requires_budget_confirmation(self) -> None:
        expensive = replace(
            self.cloud,
            id="cloud.expensive",
            cost_class=CostClass.HIGH,
        )
        with self.assertRaises(RoutingError):
            self.router.route(self.request(), (expensive,))

        decision = self.router.route(
            self.request(budget_confirmed=True), (expensive,)
        )
        self.assertEqual(decision.selected.id, "cloud.expensive")


if __name__ == "__main__":
    unittest.main()
