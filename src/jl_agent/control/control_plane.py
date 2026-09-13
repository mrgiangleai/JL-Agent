"""Thin orchestration of JL control contracts around the Hermes core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .health import HealthMonitor, ProbeOutcome
from .hermes_projection import HermesIdentity, HermesProjection
from .permissions import (
    ActionProposal,
    DecisionOutcome,
    PermissionDecision,
    PermissionRiskEngine,
)
from .registry import CapabilityDescriptor, CapabilityRegistry, HealthState
from .router import (
    DeterministicModelRouter,
    ModelCandidate,
    RouteDecision,
    RouteRequest,
)


class ControlPlaneError(RuntimeError):
    """Raised when the control path cannot safely prepare an invocation."""


@dataclass(frozen=True, slots=True)
class ControlRequest:
    capability_id: str
    action: ActionProposal
    route: RouteRequest
    candidates: tuple[ModelCandidate, ...]
    configured_keys: frozenset[str] = frozenset()
    dependency_states: Mapping[str, HealthState] | None = None
    probe: ProbeOutcome | None = None
    allow_degraded_capability: bool = False


@dataclass(frozen=True, slots=True)
class HermesInvocationProjection:
    """Policy-approved references for Hermes; this object executes nothing."""

    capability_id: str
    capability_version: str
    entrypoint_kind: str
    entrypoint_address: str
    provider: str
    model: str
    fallback_candidate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ControlPathResult:
    identity: HermesIdentity
    registry: CapabilityRegistry
    capability: CapabilityDescriptor
    health_reasons: tuple[str, ...]
    permission: PermissionDecision
    route: RouteDecision | None
    invocation: HermesInvocationProjection | None


class JLControlPlane:
    def __init__(
        self,
        *,
        projection: HermesProjection,
        router: DeterministicModelRouter,
        health_monitor: HealthMonitor | None = None,
        permission_engine: PermissionRiskEngine | None = None,
    ) -> None:
        self.projection = projection
        self.router = router
        self.health_monitor = health_monitor or HealthMonitor()
        self.permission_engine = permission_engine or PermissionRiskEngine()

    def prepare(self, request: ControlRequest) -> ControlPathResult:
        projected = self.projection.project()
        dependency_states = {
            "core.hermes.agent": HealthState.HEALTHY,
            **dict(request.dependency_states or {}),
        }
        healthy_registry, all_health_reasons = self.health_monitor.assess_registry(
            projected.registry,
            availability=projected.availability,
            configured_keys={request.capability_id: request.configured_keys},
            dependency_states=dependency_states,
            probes={request.capability_id: request.probe} if request.probe else None,
        )
        try:
            capability = healthy_registry.get(request.capability_id)
        except KeyError as error:
            raise ControlPlaneError(
                f"capability is not projected by Hermes: {request.capability_id}"
            ) from error
        allowed_health = {HealthState.HEALTHY}
        if request.allow_degraded_capability:
            allowed_health.add(HealthState.DEGRADED)
        if capability.health.state not in allowed_health:
            raise ControlPlaneError(
                "capability health does not allow routing: "
                f"{capability.health.state.value}"
            )

        permission = self.permission_engine.evaluate(capability, request.action)
        if permission.outcome is not DecisionOutcome.MAY_PROCEED:
            return ControlPathResult(
                identity=projected.identity,
                registry=healthy_registry,
                capability=capability,
                health_reasons=all_health_reasons[capability.id],
                permission=permission,
                route=None,
                invocation=None,
            )

        route = self.router.route(request.route, request.candidates)
        invocation = HermesInvocationProjection(
            capability_id=capability.id,
            capability_version=capability.version,
            entrypoint_kind=capability.entrypoint.kind.value,
            entrypoint_address=capability.entrypoint.address,
            provider=route.selected.provider,
            model=route.selected.model,
            fallback_candidate_ids=tuple(
                candidate.id for candidate in route.fallback_chain
            ),
        )
        return ControlPathResult(
            identity=projected.identity,
            registry=healthy_registry,
            capability=capability,
            health_reasons=all_health_reasons[capability.id],
            permission=permission,
            route=route,
            invocation=invocation,
        )
