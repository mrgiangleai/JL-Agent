"""Deterministic permission and action-risk decisions for JL capabilities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .registry import CapabilityDescriptor


class ActionClass(StrEnum):
    READ_ONLY = "read-only"
    REVERSIBLE_LOCAL = "reversible-local"
    DESTRUCTIVE = "destructive"
    EXTERNAL_COMMUNICATION = "external-communication"
    CREDENTIAL_SENSITIVE = "credential-sensitive"
    FINANCIAL_HIGH_RISK = "financial-high-risk"


class DecisionOutcome(StrEnum):
    MAY_PROCEED = "may-proceed"
    REQUIRES_CONFIRMATION = "requires-confirmation"
    MUST_BE_DENIED = "must-be-denied"


SCOPE_CLASSES: dict[str, ActionClass] = {
    "local.read": ActionClass.READ_ONLY,
    "network.read": ActionClass.READ_ONLY,
    "screen.capture": ActionClass.READ_ONLY,
    "local.write.reversible": ActionClass.REVERSIBLE_LOCAL,
    "input.control": ActionClass.REVERSIBLE_LOCAL,
    "local.delete": ActionClass.DESTRUCTIVE,
    "external.send": ActionClass.EXTERNAL_COMMUNICATION,
    "credential.use": ActionClass.CREDENTIAL_SENSITIVE,
    "finance.transact": ActionClass.FINANCIAL_HIGH_RISK,
}

_CLASS_ORDER = {
    action_class: index for index, action_class in enumerate(ActionClass)
}
_ALWAYS_CONFIRM = {
    ActionClass.DESTRUCTIVE,
    ActionClass.EXTERNAL_COMMUNICATION,
    ActionClass.CREDENTIAL_SENSITIVE,
    ActionClass.FINANCIAL_HIGH_RISK,
}
_NEVER_UNATTENDED = _ALWAYS_CONFIRM
_SENSITIVE_SCOPES = {"screen.capture", "input.control"}
_COMPUTER_USE_REQUIRED_SCOPES = {
    "capture": frozenset({"screen.capture"}),
    "list_apps": frozenset({"local.read"}),
    "list_windows": frozenset({"local.read"}),
    "wait": frozenset({"local.read"}),
    "click": frozenset({"input.control"}),
    "double_click": frozenset({"input.control"}),
    "right_click": frozenset({"input.control"}),
    "middle_click": frozenset({"input.control"}),
    "drag": frozenset({"input.control"}),
    "scroll": frozenset({"input.control"}),
    "type": frozenset({"input.control"}),
    "key": frozenset({"input.control"}),
    "set_value": frozenset({"input.control"}),
    "focus_app": frozenset({"input.control"}),
}
COMPUTER_USE_MUTATING_ACTIONS = frozenset(
    action
    for action, scopes in _COMPUTER_USE_REQUIRED_SCOPES.items()
    if "input.control" in scopes
)


@dataclass(frozen=True, slots=True)
class ActionProposal:
    action: str
    normalized_arguments: Mapping[str, Any]
    requested_permissions: tuple[str, ...]
    resolved_target: str = ""
    caller: str = "local-user"
    session: str = ""
    foreground_app: str = ""
    risk_hints: tuple[ActionClass, ...] = ()
    target_within_workspace: bool = True
    reversible: bool = True
    ambiguous: bool = False
    sensitive_scope: bool = False
    remote_disclosure: bool = False
    unattended: bool = False
    approval_surface_available: bool = True
    upstream_denied: bool = False


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    outcome: DecisionOutcome
    action_classes: tuple[ActionClass, ...]
    reasons: tuple[str, ...]
    binding_fingerprint: str


class PermissionRiskEngine:
    """Apply SECURITY_MODEL.md without executing or approving an action."""

    def evaluate(
        self,
        capability: CapabilityDescriptor,
        proposal: ActionProposal,
    ) -> PermissionDecision:
        fingerprint = _binding_fingerprint(capability, proposal)
        requested = set(proposal.requested_permissions)
        unknown = sorted(requested.difference(SCOPE_CLASSES))
        excessive = sorted(requested.difference(capability.permissions))
        classes = _classify(requested, proposal.risk_hints)

        denial_reasons: list[str] = []
        denial_reasons.extend(_computer_use_denials(capability, proposal, requested))
        if proposal.upstream_denied:
            denial_reasons.append("Hermes or an upstream boundary denied the action")
        if unknown:
            denial_reasons.append(f"unknown permission scopes: {', '.join(unknown)}")
        if excessive:
            denial_reasons.append(
                f"permissions exceed the capability descriptor: {', '.join(excessive)}"
            )
        if not requested and not proposal.risk_hints:
            denial_reasons.append("action has no declared permission or risk class")
        if proposal.unattended and set(classes).intersection(_NEVER_UNATTENDED):
            denial_reasons.append("risk class does not permit unattended execution")
        if denial_reasons:
            return PermissionDecision(
                outcome=DecisionOutcome.MUST_BE_DENIED,
                action_classes=classes,
                reasons=tuple(denial_reasons),
                binding_fingerprint=fingerprint,
            )

        confirmation_reasons: list[str] = []
        if set(classes).intersection(_ALWAYS_CONFIRM):
            confirmation_reasons.append("risk class always requires confirmation")
        if proposal.sensitive_scope or requested.intersection(_SENSITIVE_SCOPES):
            confirmation_reasons.append("action enters a sensitive or protected scope")
        if proposal.remote_disclosure:
            confirmation_reasons.append("action may disclose private data remotely")
        if ActionClass.REVERSIBLE_LOCAL in classes and (
            not proposal.target_within_workspace
            or not proposal.reversible
            or proposal.ambiguous
        ):
            confirmation_reasons.append(
                "local change is outside the scoped reversible workspace boundary"
            )
        if confirmation_reasons:
            outcome = DecisionOutcome.REQUIRES_CONFIRMATION
            if not proposal.approval_surface_available:
                outcome = DecisionOutcome.MUST_BE_DENIED
                confirmation_reasons.append("approval surface is unavailable")
            return PermissionDecision(
                outcome=outcome,
                action_classes=classes,
                reasons=tuple(confirmation_reasons),
                binding_fingerprint=fingerprint,
            )

        return PermissionDecision(
            outcome=DecisionOutcome.MAY_PROCEED,
            action_classes=classes,
            reasons=("action is within declared, low-risk scope",),
            binding_fingerprint=fingerprint,
        )


def _classify(
    requested_permissions: set[str],
    risk_hints: tuple[ActionClass, ...],
) -> tuple[ActionClass, ...]:
    classes = {
        SCOPE_CLASSES[scope]
        for scope in requested_permissions
        if scope in SCOPE_CLASSES
    }
    classes.update(risk_hints)
    return tuple(sorted(classes, key=_CLASS_ORDER.__getitem__))


def _computer_use_denials(
    capability: CapabilityDescriptor,
    proposal: ActionProposal,
    requested: set[str],
) -> tuple[str, ...]:
    if capability.id != "core.hermes.computer-use":
        return ()
    if proposal.action != "computer_use":
        return ("computer-use capability must invoke the exact Hermes tool",)
    inner = proposal.normalized_arguments.get("action")
    if not isinstance(inner, str) or inner not in _COMPUTER_USE_REQUIRED_SCOPES:
        return ("computer-use action is missing or unsupported",)
    required = _COMPUTER_USE_REQUIRED_SCOPES[inner]
    missing = sorted(required.difference(requested))
    reasons = []
    if missing:
        reasons.append(
            f"computer-use action requires scopes: {', '.join(missing)}"
        )
    if inner in COMPUTER_USE_MUTATING_ACTIONS:
        app = proposal.normalized_arguments.get("app")
        if not isinstance(app, str) or not app.strip():
            reasons.append("mutating computer-use action requires an exact app target")
        elif proposal.resolved_target != app.strip():
            reasons.append("computer-use app target does not match resolved target")
        if not proposal.foreground_app.strip():
            reasons.append("mutating computer-use action requires foreground context")
        if proposal.unattended:
            reasons.append("mutating computer-use action cannot run unattended")
    return tuple(reasons)


def _binding_fingerprint(
    capability: CapabilityDescriptor,
    proposal: ActionProposal,
) -> str:
    payload = {
        "action": proposal.action,
        "arguments": proposal.normalized_arguments,
        "caller": proposal.caller,
        "capability_id": capability.id,
        "capability_version": capability.version,
        "foreground_app": proposal.foreground_app,
        "permissions": sorted(proposal.requested_permissions),
        "resolved_target": proposal.resolved_target,
        "session": proposal.session,
    }
    try:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("normalized arguments must be JSON-safe") from error
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
