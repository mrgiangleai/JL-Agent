"""Deterministic pre-turn model routing; Hermes remains the executor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any, Self

import yaml

from .registry import HealthState


class TaskCategory(StrEnum):
    SIMPLE = "simple"
    CODING = "coding"
    VISION = "vision"
    WEB_RESEARCH = "web_research"
    HIGH_REASONING = "high_reasoning"


class CostClass(IntEnum):
    FREE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass(frozen=True, slots=True)
class RouterPolicy:
    prefer_local: bool = True
    allow_silent_cloud_escalation: bool = False
    require_budget_confirmation: bool = True
    max_attempts: int = 2
    preserve_locality_constraint: bool = True
    preserve_data_policy: bool = True
    preserve_minimum_capabilities: bool = True

    @classmethod
    def from_file(cls, path: str | Path) -> Self:
        try:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise ValueError(f"cannot load router policy: {error}") from error
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("router schema_version must be 1")
        policy = _mapping(data.get("policy"), "policy")
        fallback = _mapping(data.get("fallback"), "fallback")
        max_attempts = fallback.get("max_attempts")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise ValueError("fallback.max_attempts must be an integer")
        if max_attempts < 1:
            raise ValueError("fallback.max_attempts must be positive")
        return cls(
            prefer_local=_boolean(policy, "prefer_local"),
            allow_silent_cloud_escalation=_boolean(
                policy, "allow_silent_cloud_escalation"
            ),
            require_budget_confirmation=_boolean(
                policy, "require_budget_confirmation"
            ),
            max_attempts=max_attempts,
            preserve_locality_constraint=_boolean(
                fallback, "preserve_locality_constraint"
            ),
            preserve_data_policy=_boolean(fallback, "preserve_data_policy"),
            preserve_minimum_capabilities=_boolean(
                fallback, "preserve_minimum_capabilities"
            ),
        )


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    id: str
    provider: str
    model: str
    abilities: frozenset[str]
    health: HealthState
    enabled: bool = True
    local: bool = False
    data_residency: str = "remote"
    cost_class: CostClass = CostClass.MEDIUM
    latency_ms: int = 1000
    quality: int = 50
    reliability: int = 50
    context_window: int = 8192


@dataclass(frozen=True, slots=True)
class RouteRequest:
    category: TaskCategory
    required_abilities: frozenset[str]
    local_only: bool = False
    off_device_allowed: bool = True
    allowed_providers: frozenset[str] = frozenset()
    allowed_residencies: frozenset[str] = frozenset()
    maximum_cost: CostClass = CostClass.HIGH
    minimum_context: int = 0
    explicit_candidate_id: str | None = None
    budget_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class RejectedCandidate:
    candidate_id: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RouteDecision:
    category: TaskCategory
    selected: ModelCandidate
    fallback_chain: tuple[ModelCandidate, ...]
    reasons: tuple[str, ...]
    rejected_candidates: tuple[RejectedCandidate, ...]
    estimated_cost_class: CostClass

    @property
    def ordered_chain(self) -> tuple[ModelCandidate, ...]:
        return (self.selected, *self.fallback_chain)


class RoutingError(RuntimeError):
    def __init__(
        self, message: str, rejected_candidates: tuple[RejectedCandidate, ...]
    ) -> None:
        super().__init__(message)
        self.rejected_candidates = rejected_candidates


class DeterministicModelRouter:
    def __init__(self, policy: RouterPolicy) -> None:
        self.policy = policy

    def route(
        self,
        request: RouteRequest,
        candidates: tuple[ModelCandidate, ...],
    ) -> RouteDecision:
        accepted: list[ModelCandidate] = []
        rejected: list[RejectedCandidate] = []
        seen_ids: set[str] = set()
        for candidate in candidates:
            if candidate.id in seen_ids:
                raise RoutingError(
                    f"duplicate model candidate ID: {candidate.id}", tuple(rejected)
                )
            seen_ids.add(candidate.id)
            reasons = self._rejection_reasons(request, candidate)
            if reasons:
                rejected.append(RejectedCandidate(candidate.id, tuple(reasons)))
            else:
                accepted.append(candidate)

        explicit = request.explicit_candidate_id
        if explicit:
            selected = next((item for item in accepted if item.id == explicit), None)
            if selected is None:
                raise RoutingError(
                    f"explicit model candidate is unavailable: {explicit}",
                    tuple(rejected),
                )
            remainder = [item for item in accepted if item.id != explicit]
            ordered = [
                selected,
                *sorted(remainder, key=lambda item: self._score(request, item)),
            ]
            decision_reasons = ("honored explicit provider/model choice",)
        else:
            ordered = sorted(accepted, key=lambda item: self._score(request, item))
            if not ordered:
                raise RoutingError(
                    "no policy-compliant model candidate", tuple(rejected)
                )
            selected = ordered[0]
            decision_reasons = (
                f"selected deterministically for {request.category.value}",
            )

        chain = self._bounded_fallbacks(selected, ordered[1:])
        return RouteDecision(
            category=request.category,
            selected=selected,
            fallback_chain=chain,
            reasons=decision_reasons,
            rejected_candidates=tuple(rejected),
            estimated_cost_class=selected.cost_class,
        )

    def _rejection_reasons(
        self, request: RouteRequest, candidate: ModelCandidate
    ) -> list[str]:
        reasons: list[str] = []
        if not candidate.enabled:
            reasons.append("candidate is disabled")
        if candidate.health not in {HealthState.HEALTHY, HealthState.DEGRADED}:
            reasons.append(f"candidate health is {candidate.health.value}")
        missing = sorted(request.required_abilities.difference(candidate.abilities))
        if missing:
            reasons.append(f"missing abilities: {', '.join(missing)}")
        if (
            request.allowed_providers
            and candidate.provider not in request.allowed_providers
        ):
            reasons.append("provider is not allowlisted")
        if request.local_only and not candidate.local:
            reasons.append("request is local-only")
        if not request.off_device_allowed and not candidate.local:
            reasons.append("off-device processing is not allowed")
        if (
            request.allowed_residencies
            and candidate.data_residency not in request.allowed_residencies
        ):
            reasons.append("candidate violates data-residency policy")
        if candidate.cost_class > request.maximum_cost:
            reasons.append("candidate exceeds maximum cost")
        if (
            candidate.cost_class is CostClass.HIGH
            and self.policy.require_budget_confirmation
            and not request.budget_confirmed
        ):
            reasons.append("high-cost route requires budget confirmation")
        if candidate.context_window < request.minimum_context:
            reasons.append("candidate context window is too small")
        return reasons

    def _score(
        self, request: RouteRequest, candidate: ModelCandidate
    ) -> tuple[int | str, ...]:
        health_penalty = 1 if candidate.health is HealthState.DEGRADED else 0
        locality = 0 if candidate.local and self.policy.prefer_local else 1
        cost = int(candidate.cost_class)
        if request.category is TaskCategory.SIMPLE:
            profile = (locality, candidate.latency_ms, cost)
        elif request.category is TaskCategory.CODING:
            profile = (
                -candidate.quality,
                -candidate.context_window,
                candidate.latency_ms,
            )
        elif request.category is TaskCategory.VISION:
            profile = (-candidate.quality, locality, candidate.latency_ms)
        elif request.category is TaskCategory.WEB_RESEARCH:
            profile = (-candidate.quality, -candidate.reliability, cost)
        else:
            profile = (-candidate.quality, -candidate.reliability, cost)
        return (
            health_penalty,
            *profile,
            candidate.provider,
            candidate.model,
            candidate.id,
        )

    def _bounded_fallbacks(
        self,
        selected: ModelCandidate,
        ordered: list[ModelCandidate],
    ) -> tuple[ModelCandidate, ...]:
        if not self.policy.allow_silent_cloud_escalation and selected.local:
            ordered = [candidate for candidate in ordered if candidate.local]
        return tuple(ordered[: self.policy.max_attempts - 1])


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _boolean(data: Mapping[str, Any], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value
