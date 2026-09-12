"""Cheap, side-effect-free capability health evaluation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from .registry import CapabilityDescriptor, CapabilityRegistry, Health, HealthState


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """A precomputed adapter observation; the monitor never starts the adapter."""

    state: HealthState
    detail: str = ""

    def __post_init__(self) -> None:
        allowed = {
            HealthState.HEALTHY,
            HealthState.DEGRADED,
            HealthState.UNAVAILABLE,
            HealthState.BLOCKED,
        }
        if self.state not in allowed:
            raise ValueError(f"probe outcome cannot be {self.state}")


@dataclass(frozen=True, slots=True)
class HealthAssessment:
    capability: CapabilityDescriptor
    reasons: tuple[str, ...]


class HealthMonitor:
    """Derive registry health from bounded, non-billable observations."""

    def assess(
        self,
        capability: CapabilityDescriptor,
        *,
        available: bool,
        configured_keys: frozenset[str] = frozenset(),
        dependency_states: Mapping[str, HealthState] | None = None,
        probe: ProbeOutcome | None = None,
    ) -> HealthAssessment:
        dependencies = dependency_states or {}
        state, reasons = self._derive_state(
            capability,
            available=available,
            configured_keys=configured_keys,
            dependency_states=dependencies,
            probe=probe,
        )
        updated = replace(
            capability,
            health=Health(
                state=state,
                check=capability.health.check,
                timeout_seconds=capability.health.timeout_seconds,
            ),
        )
        return HealthAssessment(capability=updated, reasons=reasons)

    def assess_registry(
        self,
        registry: CapabilityRegistry,
        *,
        availability: Mapping[str, bool],
        configured_keys: Mapping[str, frozenset[str]] | None = None,
        dependency_states: Mapping[str, HealthState] | None = None,
        probes: Mapping[str, ProbeOutcome] | None = None,
    ) -> tuple[CapabilityRegistry, dict[str, tuple[str, ...]]]:
        configs = configured_keys or {}
        states = dict(dependency_states or {})
        outcomes = probes or {}
        assessed: list[CapabilityDescriptor] = []
        reasons: dict[str, tuple[str, ...]] = {}
        for capability in registry.capabilities:
            result = self.assess(
                capability,
                available=availability.get(capability.id, False),
                configured_keys=configs.get(capability.id, frozenset()),
                dependency_states=states,
                probe=outcomes.get(capability.id),
            )
            assessed.append(result.capability)
            reasons[capability.id] = result.reasons
            states[capability.id] = result.capability.health.state
        return (
            CapabilityRegistry(
                schema_version=registry.schema_version,
                capabilities=tuple(assessed),
            ),
            reasons,
        )

    @staticmethod
    def _derive_state(
        capability: CapabilityDescriptor,
        *,
        available: bool,
        configured_keys: frozenset[str],
        dependency_states: Mapping[str, HealthState],
        probe: ProbeOutcome | None,
    ) -> tuple[HealthState, tuple[str, ...]]:
        if not capability.enabled:
            return HealthState.DISABLED, ("capability is disabled by operator policy",)

        missing_config = sorted(
            set(capability.configuration_requirements.required) - configured_keys
        )
        if missing_config:
            return (
                HealthState.MISCONFIGURED,
                (f"missing required configuration: {', '.join(missing_config)}",),
            )

        if not available:
            return HealthState.UNAVAILABLE, ("implementation is not available",)

        missing_dependencies = sorted(
            dependency
            for dependency in capability.dependencies
            if dependency not in dependency_states
        )
        if missing_dependencies:
            return (
                HealthState.UNAVAILABLE,
                (f"dependency state is missing: {', '.join(missing_dependencies)}",),
            )

        hard_failure_states = {
            HealthState.UNAVAILABLE,
            HealthState.MISCONFIGURED,
            HealthState.DISABLED,
            HealthState.BLOCKED,
        }
        failed_dependencies = sorted(
            dependency
            for dependency in capability.dependencies
            if dependency_states[dependency] in hard_failure_states
        )
        if failed_dependencies:
            return (
                HealthState.UNAVAILABLE,
                (f"dependencies are unavailable: {', '.join(failed_dependencies)}",),
            )

        soft_dependencies = sorted(
            dependency
            for dependency in capability.dependencies
            if dependency_states[dependency]
            in {HealthState.DEGRADED, HealthState.UNKNOWN, HealthState.STARTING}
        )
        if probe and probe.state in {HealthState.UNAVAILABLE, HealthState.BLOCKED}:
            return HealthState.UNAVAILABLE, (probe.detail or "probe is unavailable",)
        if soft_dependencies or (probe and probe.state is HealthState.DEGRADED):
            details = []
            if soft_dependencies:
                details.append(
                    f"dependencies are degraded: {', '.join(soft_dependencies)}"
                )
            if probe and probe.state is HealthState.DEGRADED:
                details.append(probe.detail or "probe reported degraded service")
            return HealthState.DEGRADED, tuple(details)
        return HealthState.HEALTHY, ("cheap availability checks passed",)
