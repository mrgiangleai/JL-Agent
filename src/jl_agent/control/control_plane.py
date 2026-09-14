"""Thin orchestration of JL control contracts around the Hermes core."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
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
    """Exact, inert Hermes turn projection; this object is not authorization."""

    capability_id: str
    capability_version: str
    entrypoint_kind: str
    entrypoint_address: str
    provider: str
    model: str
    route_candidate_id: str
    fallback_candidate_ids: tuple[str, ...]
    fallback_routes: tuple[tuple[str, str], ...]
    action: str
    arguments_json: str
    caller_id: str
    session_id: str
    binding_fingerprint: str


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
        trusted_probe_provider: Callable[[str], ProbeOutcome | None] | None = None,
    ) -> None:
        self.projection = projection
        self.router = router
        self.health_monitor = health_monitor or HealthMonitor()
        self.permission_engine = permission_engine or PermissionRiskEngine()
        self.trusted_probe_provider = trusted_probe_provider

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
        probe = request.probe
        if request.capability_id == "core.hermes.computer-use":
            probe = (
                self.trusted_probe_provider(request.capability_id)
                if self.trusted_probe_provider is not None
                else ProbeOutcome(
                    HealthState.UNAVAILABLE,
                    "trusted computer-use readiness probe is unavailable",
                )
            )
        healthy_registry, all_health_reasons = self.health_monitor.assess_registry(
            projected.registry,
            availability=projected.availability,
            configured_keys={request.capability_id: request.configured_keys},
            dependency_states=dependency_states,
            probes={request.capability_id: probe} if probe else None,
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

    def revalidate_route(self, request: ControlRequest) -> ControlPathResult:
        """Recompute policy, health, identity, and route without granting approval."""
        checked = self.check_policy(request)
        if checked.permission.outcome is DecisionOutcome.MUST_BE_DENIED:
            return checked
        return self._prepare_route(checked, request)

    def revalidate_prepared(
        self,
        request: ControlRequest,
        expected: HermesInvocationProjection,
        *,
        consumed_approval: ApprovalRecord | None = None,
    ) -> ControlPathResult:
        """Rebuild an exact projection from fresh health, policy, and route state."""
        checked = self.check_policy(request)
        outcome = checked.permission.outcome
        if outcome is DecisionOutcome.MUST_BE_DENIED:
            raise ControlPlaneError("fresh policy denied execution")
        if outcome is DecisionOutcome.REQUIRES_CONFIRMATION:
            if consumed_approval is None:
                raise ControlPlaneError("fresh policy requires a consumed approval")
            if consumed_approval.state is not ApprovalState.CONSUMED:
                raise ControlPlaneError("approval is not consumed")
            if (
                consumed_approval.binding_fingerprint
                != checked.permission.binding_fingerprint
                or consumed_approval.caller_id != request.action.caller
                or consumed_approval.session_id != request.action.session
            ):
                raise ControlPlaneError("consumed approval no longer matches")
        rebuilt = self._prepare_route(checked, request)
        if rebuilt.invocation != expected:
            raise ControlPlaneError("prepared Hermes projection is stale or changed")
        return rebuilt

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
            route_candidate_id=route.selected.id,
            fallback_candidate_ids=tuple(
                candidate.id for candidate in route.fallback_chain
            ),
            fallback_routes=tuple(
                (candidate.provider, candidate.model)
                for candidate in route.fallback_chain
            ),
            action=request.action.action,
            arguments_json=json.dumps(
                request.action.normalized_arguments,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ),
            caller_id=request.action.caller,
            session_id=request.action.session,
            binding_fingerprint=checked.permission.binding_fingerprint,
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
