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
