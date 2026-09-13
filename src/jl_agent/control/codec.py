"""Strict wire decoder for Phase 3B control requests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from .control_plane import ControlRequest
from .health import ProbeOutcome
from .ipc import IPCRequestEnvelope
from .permissions import ActionClass, ActionProposal
from .registry import HealthState
from .router import CostClass, ModelCandidate, RouteRequest, TaskCategory


def decode_control_request(envelope: IPCRequestEnvelope) -> ControlRequest:
    payload = _mapping(envelope.payload, "payload")
    _only(
        payload,
        {
            "capability_id",
            "action",
            "route",
            "candidates",
            "configured_keys",
            "dependency_states",
            "probe",
            "allow_degraded_capability",
            "approval_id",
        },
        "payload",
    )
    action_data = _mapping(_required(payload, "action", "payload"), "action")
    _only(
        action_data,
        {
            "action",
            "normalized_arguments",
            "requested_permissions",
            "resolved_target",
            "foreground_app",
            "risk_hints",
            "target_within_workspace",
            "reversible",
            "ambiguous",
            "sensitive_scope",
            "remote_disclosure",
            "unattended",
            "approval_surface_available",
            "upstream_denied",
        },
        "action",
    )
    action = ActionProposal(
        action=_text(_required(action_data, "action", "action"), "action.action"),
        normalized_arguments=_mapping(
            _required(action_data, "normalized_arguments", "action"),
            "action.normalized_arguments",
        ),
        requested_permissions=_strings(
            _required(action_data, "requested_permissions", "action"),
            "action.requested_permissions",
        ),
        resolved_target=_optional_text(action_data, "resolved_target"),
        caller=envelope.caller_id,
        session=envelope.session_id,
        foreground_app=_optional_text(action_data, "foreground_app"),
        risk_hints=tuple(
            ActionClass(value)
            for value in _strings(
                action_data.get("risk_hints", []), "action.risk_hints"
            )
        ),
        target_within_workspace=_boolean(action_data, "target_within_workspace", True),
        reversible=_boolean(action_data, "reversible", True),
        ambiguous=_boolean(action_data, "ambiguous", False),
        sensitive_scope=_boolean(action_data, "sensitive_scope", False),
        remote_disclosure=_boolean(action_data, "remote_disclosure", False),
        unattended=_boolean(action_data, "unattended", False),
        approval_surface_available=_boolean(
            action_data, "approval_surface_available", True
        ),
        upstream_denied=_boolean(action_data, "upstream_denied", False),
    )
    route = _decode_route(_required(payload, "route", "payload"))
    candidates_value = _required(payload, "candidates", "payload")
    if not isinstance(candidates_value, list):
        raise ValueError("payload.candidates must be an array")
    dependencies = _mapping(payload.get("dependency_states", {}), "dependency_states")
    probe_data = payload.get("probe")
    probe = None
    if probe_data is not None:
        probe_map = _mapping(probe_data, "probe")
        _only(probe_map, {"state", "detail"}, "probe")
        probe = ProbeOutcome(
            HealthState(_required(probe_map, "state", "probe")),
            _optional_text(probe_map, "detail"),
        )
    return ControlRequest(
        capability_id=_text(
            _required(payload, "capability_id", "payload"), "payload.capability_id"
        ),
        action=action,
        route=route,
        candidates=tuple(
            _decode_candidate(value, index)
            for index, value in enumerate(candidates_value)
        ),
        configured_keys=frozenset(
            _strings(payload.get("configured_keys", []), "payload.configured_keys")
        ),
        dependency_states={
            _text(key, "dependency ID"): HealthState(value)
            for key, value in dependencies.items()
        },
        probe=probe,
        allow_degraded_capability=_boolean(payload, "allow_degraded_capability", False),
    )


def _decode_route(value: object) -> RouteRequest:
    data = _mapping(value, "route")
    _only(
        data,
        {
            "category",
            "required_abilities",
            "local_only",
            "off_device_allowed",
            "allowed_providers",
            "allowed_residencies",
            "maximum_cost",
            "minimum_context",
            "explicit_candidate_id",
            "budget_confirmed",
        },
        "route",
    )
    minimum_context = data.get("minimum_context", 0)
    if isinstance(minimum_context, bool) or not isinstance(minimum_context, int):
        raise ValueError("route.minimum_context must be an integer")
    explicit = data.get("explicit_candidate_id")
    if explicit is not None:
        explicit = _text(explicit, "route.explicit_candidate_id")
    return RouteRequest(
        category=TaskCategory(_required(data, "category", "route")),
        required_abilities=frozenset(
            _strings(
                _required(data, "required_abilities", "route"),
                "route.required_abilities",
            )
        ),
        local_only=_boolean(data, "local_only", False),
        off_device_allowed=_boolean(data, "off_device_allowed", True),
        allowed_providers=frozenset(
            _strings(data.get("allowed_providers", []), "route.allowed_providers")
        ),
        allowed_residencies=frozenset(
            _strings(data.get("allowed_residencies", []), "route.allowed_residencies")
        ),
        maximum_cost=CostClass(data.get("maximum_cost", int(CostClass.HIGH))),
        minimum_context=minimum_context,
        explicit_candidate_id=explicit,
        budget_confirmed=_boolean(data, "budget_confirmed", False),
    )


def _decode_candidate(value: object, index: int) -> ModelCandidate:
    path = f"candidates[{index}]"
    data = _mapping(value, path)
    _only(
        data,
        {
            "id",
            "provider",
            "model",
            "abilities",
            "health",
            "enabled",
            "local",
            "data_residency",
            "cost_class",
            "latency_ms",
            "quality",
            "reliability",
            "context_window",
        },
        path,
    )
    integers: dict[str, int] = {}
    for name, default in (
        ("latency_ms", 1000),
        ("quality", 50),
        ("reliability", 50),
        ("context_window", 8192),
    ):
        raw = data.get(name, default)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(f"{path}.{name} must be an integer")
        integers[name] = raw
    return ModelCandidate(
        id=_text(_required(data, "id", path), f"{path}.id"),
        provider=_text(_required(data, "provider", path), f"{path}.provider"),
        model=_text(_required(data, "model", path), f"{path}.model"),
        abilities=frozenset(
            _strings(_required(data, "abilities", path), f"{path}.abilities")
        ),
        health=HealthState(_required(data, "health", path)),
        enabled=_boolean(data, "enabled", True),
        local=_boolean(data, "local", False),
        data_residency=_optional_text(data, "data_residency", "remote"),
        cost_class=CostClass(data.get("cost_class", int(CostClass.MEDIUM))),
        latency_ms=integers["latency_ms"],
        quality=integers["quality"],
        reliability=integers["reliability"],
        context_window=integers["context_window"],
    )


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{path} must be an object")
    return dict(cast(Mapping[str, Any], value))


def _required(data: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in data:
        raise ValueError(f"{path}.{key} is required")
    return data[key]


def _only(data: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data).difference(allowed))
    if unknown:
        raise ValueError(f"{path} has unknown fields: {unknown}")


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError(f"{path} must be non-empty bounded text")
    return value


def _optional_text(data: Mapping[str, Any], key: str, default: str = "") -> str:
    value = data.get(key, default)
    return _text(value, key) if value else ""


def _strings(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    result = tuple(_text(item, path) for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{path} contains duplicates")
    return result


def _boolean(data: Mapping[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value
