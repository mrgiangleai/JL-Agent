"""Thin orchestration of JL control contracts around the Hermes core."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .approvals import ApprovalRecord, ApprovalState
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
        checked = self.check_policy(request)
        if checked.permission.outcome is not DecisionOutcome.MAY_PROCEED:
            return checked
        return self._prepare_route(checked, request)

    def check_policy(self, request: ControlRequest) -> ControlPathResult:
        """Evaluate health and policy without constructing an invocation."""
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
        return ControlPathResult(
            identity=projected.identity,
            registry=healthy_registry,
            capability=capability,
            health_reasons=all_health_reasons[capability.id],
            permission=permission,
            route=None,
            invocation=None,
        )

    def prepare_confirmed(
        self,
        request: ControlRequest,
        approval: ApprovalRecord,
    ) -> ControlPathResult:
        """Prepare only after rechecking policy against a consumed approval."""
        if approval.state is not ApprovalState.CONSUMED:
            raise ControlPlaneError("approval has not been consumed")
        checked = self.check_policy(request)
        if checked.permission.outcome is not DecisionOutcome.REQUIRES_CONFIRMATION:
            raise ControlPlaneError(
                "confirmed preparation requires confirmation policy"
            )
        if (
            approval.binding_fingerprint != checked.permission.binding_fingerprint
            or approval.caller_id != request.action.caller
            or approval.session_id != request.action.session
        ):
            raise ControlPlaneError("consumed approval does not match policy decision")
        return self._prepare_route(checked, request)

    def _prepare_route(
        self,
        checked: ControlPathResult,
        request: ControlRequest,
    ) -> ControlPathResult:
        route = self.router.route(request.route, request.candidates)
        invocation = HermesInvocationProjection(
            capability_id=checked.capability.id,
            capability_version=checked.capability.version,
            entrypoint_kind=checked.capability.entrypoint.kind.value,
            entrypoint_address=checked.capability.entrypoint.address,
            provider=route.selected.provider,
            model=route.selected.model,
            fallback_candidate_ids=tuple(
                candidate.id for candidate in route.fallback_chain
            ),
        )
        return ControlPathResult(
            identity=checked.identity,
            registry=checked.registry,
            capability=checked.capability,
            health_reasons=checked.health_reasons,
            permission=checked.permission,
            route=route,
            invocation=invocation,
        )
