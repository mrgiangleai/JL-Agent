from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from jl_agent.control.health import HealthMonitor, ProbeOutcome
from jl_agent.control.registry import (
    CapabilityRegistry,
    ConfigurationRequirements,
    HealthState,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "config" / "capabilities.example.yaml"


class HealthMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.monitor = HealthMonitor()
        self.capability = CapabilityRegistry.from_file(FIXTURE).capabilities[0]

    def test_disabled_takes_precedence(self) -> None:
        capability = replace(self.capability, enabled=False)

        result = self.monitor.assess(capability, available=False)

        self.assertEqual(result.capability.health.state, HealthState.DISABLED)

    def test_missing_required_configuration_is_misconfigured(self) -> None:
        capability = replace(
            self.capability,
            configuration_requirements=ConfigurationRequirements(
                required=("provider",), optional=()
            ),
        )

        result = self.monitor.assess(capability, available=True)

        self.assertEqual(result.capability.health.state, HealthState.MISCONFIGURED)
        self.assertIn("provider", result.reasons[0])

    def test_missing_implementation_or_dependency_is_unavailable(self) -> None:
        unavailable = self.monitor.assess(self.capability, available=False)
        capability = replace(self.capability, dependencies=("runtime.core",))
        missing_dependency = self.monitor.assess(capability, available=True)

        self.assertEqual(
            unavailable.capability.health.state, HealthState.UNAVAILABLE
        )
        self.assertEqual(
            missing_dependency.capability.health.state, HealthState.UNAVAILABLE
        )

    def test_probe_and_dependency_can_degrade_capability(self) -> None:
        capability = replace(self.capability, dependencies=("runtime.core",))
        result = self.monitor.assess(
            capability,
            available=True,
            dependency_states={"runtime.core": HealthState.HEALTHY},
            probe=ProbeOutcome(HealthState.DEGRADED, "cache is stale"),
        )

        self.assertEqual(result.capability.health.state, HealthState.DEGRADED)
        self.assertEqual(result.reasons, ("cache is stale",))

    def test_probe_can_report_disabled_or_misconfigured(self) -> None:
        dependencies = {"python>=3.11,<3.14": HealthState.HEALTHY}
        disabled = self.monitor.assess(
            self.capability,
            available=True,
            dependency_states=dependencies,
            probe=ProbeOutcome(HealthState.DISABLED, "operator disabled it"),
        )
        misconfigured = self.monitor.assess(
            self.capability,
            available=True,
            dependency_states=dependencies,
            probe=ProbeOutcome(HealthState.MISCONFIGURED, "bad manifest"),
        )

        self.assertEqual(disabled.capability.health.state, HealthState.DISABLED)
        self.assertEqual(
            misconfigured.capability.health.state, HealthState.MISCONFIGURED
        )

    def test_all_checks_pass_as_healthy(self) -> None:
        result = self.monitor.assess(
            self.capability,
            available=True,
            dependency_states={
                "python>=3.11,<3.14": HealthState.HEALTHY,
            },
        )

        self.assertEqual(result.capability.health.state, HealthState.HEALTHY)

    def test_registry_assessment_tracks_prior_dependency_results(self) -> None:
        root = replace(self.capability, id="runtime.core", dependencies=())
        child = replace(
            self.capability, id="runtime.child", dependencies=("runtime.core",)
        )
        registry = CapabilityRegistry(1, (root, child))

        assessed, _reasons = self.monitor.assess_registry(
            registry,
            availability={"runtime.core": True, "runtime.child": True},
        )

        self.assertTrue(
            all(
                item.health.state is HealthState.HEALTHY
                for item in assessed.capabilities
            )
        )


if __name__ == "__main__":
    unittest.main()
