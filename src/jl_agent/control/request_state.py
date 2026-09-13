"""Deterministic authenticated request lifecycle for Phase 3A."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .approvals import ApprovalError, OneTimeApprovalStore
from .auth import CredentialProvider
from .control_plane import (
    ControlPlaneError,
    ControlRequest,
    HermesInvocationProjection,
    JLControlPlane,
)
from .ipc import IPCRequestEnvelope, IPCResponseEnvelope
from .permissions import DecisionOutcome
from .router import RoutingError


class RequestState(StrEnum):
    RECEIVED = "received"
    AUTHENTICATED = "authenticated"
    POLICY_CHECKED = "policy_checked"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PREPARED = "prepared"
    DENIED = "denied"
    FAILED = "failed"


_LEGAL_TRANSITIONS: dict[RequestState, frozenset[RequestState]] = {
    RequestState.RECEIVED: frozenset(
        {RequestState.AUTHENTICATED, RequestState.DENIED, RequestState.FAILED}
    ),
    RequestState.AUTHENTICATED: frozenset(
        {RequestState.POLICY_CHECKED, RequestState.DENIED, RequestState.FAILED}
    ),
    RequestState.POLICY_CHECKED: frozenset(
        {
            RequestState.AWAITING_APPROVAL,
            RequestState.APPROVED,
            RequestState.DENIED,
            RequestState.FAILED,
        }
    ),
    RequestState.AWAITING_APPROVAL: frozenset(
        {RequestState.APPROVED, RequestState.DENIED, RequestState.FAILED}
    ),
    RequestState.APPROVED: frozenset(
        {RequestState.PREPARED, RequestState.DENIED, RequestState.FAILED}
    ),
    RequestState.PREPARED: frozenset(),
    RequestState.DENIED: frozenset(),
    RequestState.FAILED: frozenset(),
}


class IllegalRequestTransition(RuntimeError):
    """Raised when code attempts to bypass a lifecycle gate."""


@dataclass(slots=True)
class RequestLifecycle:
    request_id: str
    state: RequestState = RequestState.RECEIVED
    history: list[RequestState] = field(
        default_factory=lambda: [RequestState.RECEIVED]
    )

    def transition(self, target: RequestState) -> None:
        if target not in _LEGAL_TRANSITIONS[self.state]:
            raise IllegalRequestTransition(
                f"illegal request transition: {self.state.value} -> {target.value}"
            )
        self.state = target
        self.history.append(target)


RequestDecoder = Callable[[IPCRequestEnvelope], ControlRequest]


class SecureControlRequestHandler:
    """Authenticate and prepare policy-approved references without execution."""

    def __init__(
        self,
        *,
        credentials: CredentialProvider,
        approvals: OneTimeApprovalStore,
        control_plane: JLControlPlane,
        request_decoder: RequestDecoder,
    ) -> None:
        self.credentials = credentials
        self.approvals = approvals
        self.control_plane = control_plane
        self.request_decoder = request_decoder

    def __call__(self, envelope: IPCRequestEnvelope) -> IPCResponseEnvelope:
        lifecycle = RequestLifecycle(envelope.request_id)
        credential = envelope.credential
        if credential is None or not self.credentials.authenticate(credential):
            lifecycle.transition(RequestState.DENIED)
            return _failure(envelope, lifecycle, "authentication_failed")
        lifecycle.transition(RequestState.AUTHENTICATED)

        if envelope.operation != "prepare":
            lifecycle.transition(RequestState.FAILED)
            return _failure(envelope, lifecycle, "unsupported_operation")
        try:
            request = self.request_decoder(envelope)
            if not isinstance(request, ControlRequest):
                raise TypeError("decoder did not return a ControlRequest")
            approval_id = _approval_id(envelope.payload)
        except (TypeError, ValueError):
            lifecycle.transition(RequestState.FAILED)
            return _failure(envelope, lifecycle, "malformed_payload")

        if (
            request.action.caller != envelope.caller_id
            or request.action.session != envelope.session_id
        ):
            lifecycle.transition(RequestState.DENIED)
            return _failure(envelope, lifecycle, "identity_mismatch")

        try:
            checked = self.control_plane.check_policy(request)
        except ControlPlaneError:
            lifecycle.transition(RequestState.DENIED)
            return _failure(envelope, lifecycle, "policy_denied")
        except (RuntimeError, ValueError):
            lifecycle.transition(RequestState.FAILED)
            return _failure(envelope, lifecycle, "preparation_failed")
        lifecycle.transition(RequestState.POLICY_CHECKED)

        outcome = checked.permission.outcome
        if outcome is DecisionOutcome.MUST_BE_DENIED:
            lifecycle.transition(RequestState.DENIED)
            return _failure(envelope, lifecycle, "policy_denied")

        if outcome is DecisionOutcome.MAY_PROCEED:
            lifecycle.transition(RequestState.APPROVED)
            try:
                prepared = self.control_plane.prepare(request)
            except RoutingError:
                lifecycle.transition(RequestState.FAILED)
                return _failure(envelope, lifecycle, "preparation_failed")
            except ControlPlaneError:
                lifecycle.transition(RequestState.DENIED)
                return _failure(envelope, lifecycle, "policy_denied")
            if (
                prepared.permission.outcome is not DecisionOutcome.MAY_PROCEED
                or prepared.invocation is None
            ):
                lifecycle.transition(RequestState.DENIED)
                return _failure(envelope, lifecycle, "policy_denied")
            lifecycle.transition(RequestState.PREPARED)
            return _prepared_response(envelope, lifecycle, prepared.invocation)

        lifecycle.transition(RequestState.AWAITING_APPROVAL)
        if approval_id is None:
            return IPCResponseEnvelope.success(
                envelope.request_id,
                {
                    "state": lifecycle.state.value,
                    "history": [state.value for state in lifecycle.history],
                    "approval_fingerprint": checked.permission.binding_fingerprint,
                },
            )
        try:
            consumed = self.approvals.consume(
                approval_id,
                binding_fingerprint=checked.permission.binding_fingerprint,
                caller_id=envelope.caller_id,
                session_id=envelope.session_id,
            )
        except ApprovalError:
            lifecycle.transition(RequestState.DENIED)
            return _failure(envelope, lifecycle, "approval_denied")
        lifecycle.transition(RequestState.APPROVED)
        try:
            prepared = self.control_plane.prepare_confirmed(request, consumed)
        except RoutingError:
            lifecycle.transition(RequestState.FAILED)
            return _failure(envelope, lifecycle, "preparation_failed")
        except ControlPlaneError:
            lifecycle.transition(RequestState.DENIED)
            return _failure(envelope, lifecycle, "policy_denied")
        if prepared.invocation is None:
            lifecycle.transition(RequestState.FAILED)
            return _failure(envelope, lifecycle, "preparation_failed")
        lifecycle.transition(RequestState.PREPARED)
        return _prepared_response(envelope, lifecycle, prepared.invocation)


def _approval_id(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("approval_id")
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("approval_id must be bounded text")
    return value


def _failure(
    envelope: IPCRequestEnvelope,
    lifecycle: RequestLifecycle,
    code: str,
) -> IPCResponseEnvelope:
    return IPCResponseEnvelope.failure(
        envelope.request_id,
        code,
        f"request ended in {lifecycle.state.value}",
    )


def _prepared_response(
    envelope: IPCRequestEnvelope,
    lifecycle: RequestLifecycle,
    invocation: HermesInvocationProjection,
) -> IPCResponseEnvelope:
    return IPCResponseEnvelope.success(
        envelope.request_id,
        {
            "state": lifecycle.state.value,
            "history": [state.value for state in lifecycle.history],
            "invocation": {
                "capability_id": invocation.capability_id,
                "capability_version": invocation.capability_version,
                "entrypoint_kind": invocation.entrypoint_kind,
                "entrypoint_address": invocation.entrypoint_address,
                "provider": invocation.provider,
                "model": invocation.model,
                "fallback_candidate_ids": list(invocation.fallback_candidate_ids),
            },
        },
    )
