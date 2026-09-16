"""Deterministic authenticated request lifecycle for Phase 3A."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from .consent import ConsentCoordinator
    from .execution import ExecutionGate


class RequestState(StrEnum):
    RECEIVED = "received"
    AUTHENTICATED = "authenticated"
    POLICY_CHECKED = "policy_checked"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PREPARED = "prepared"
    EXECUTING = "executing"
    COMPLETED = "completed"
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
    RequestState.PREPARED: frozenset({RequestState.EXECUTING, RequestState.DENIED}),
    RequestState.EXECUTING: frozenset(
        {RequestState.COMPLETED, RequestState.DENIED, RequestState.FAILED}
    ),
    RequestState.COMPLETED: frozenset(),
    RequestState.DENIED: frozenset(),
    RequestState.FAILED: frozenset(),
}


class IllegalRequestTransition(RuntimeError):
    """Raised when code attempts to bypass a lifecycle gate."""


@dataclass(slots=True)
class RequestLifecycle:
    request_id: str
    state: RequestState = RequestState.RECEIVED
    history: list[RequestState] = field(default_factory=lambda: [RequestState.RECEIVED])

    def transition(self, target: RequestState) -> None:
        if target not in _LEGAL_TRANSITIONS[self.state]:
            raise IllegalRequestTransition(
                f"illegal request transition: {self.state.value} -> {target.value}"
            )
        self.state = target
        self.history.append(target)


RequestDecoder = Callable[[IPCRequestEnvelope], ControlRequest]
StatusProvider = Callable[[], Mapping[str, Any]]
ActivityReader = Callable[[int], tuple[Mapping[str, Any], ...]]
VoiceHandler = Callable[[str, Mapping[str, Any], str, str], Mapping[str, object]]
AutomationHandler = Callable[[IPCRequestEnvelope], IPCResponseEnvelope]
AssistantHandler = Callable[[IPCRequestEnvelope], IPCResponseEnvelope]


class SecureControlRequestHandler:
    """Authenticate and prepare policy-approved references without execution."""

    def __init__(
        self,
        *,
        credentials: CredentialProvider,
        approvals: OneTimeApprovalStore,
        control_plane: JLControlPlane,
        request_decoder: RequestDecoder,
        execution_gate: ExecutionGate | None = None,
        consent_coordinator: ConsentCoordinator | None = None,
        status_provider: StatusProvider | None = None,
        activity_reader: ActivityReader | None = None,
        voice_handler: VoiceHandler | None = None,
        automation_handler: AutomationHandler | None = None,
        assistant_handler: AssistantHandler | None = None,
    ) -> None:
        self.credentials = credentials
        self.approvals = approvals
        self.control_plane = control_plane
        self.request_decoder = request_decoder
        self.execution_gate = execution_gate
        self.consent_coordinator = consent_coordinator
        self.status_provider = status_provider
        self.activity_reader = activity_reader
        self.voice_handler = voice_handler
        self.automation_handler = automation_handler
        self.assistant_handler = assistant_handler

    def __call__(self, envelope: IPCRequestEnvelope) -> IPCResponseEnvelope:
        lifecycle = RequestLifecycle(envelope.request_id)
        credential = envelope.credential
        if credential is None or not self.credentials.authenticate(credential):
            lifecycle.transition(RequestState.DENIED)
            return self._failure(envelope, lifecycle, "authentication_failed")
        lifecycle.transition(RequestState.AUTHENTICATED)

        execution_context = None
        if self.execution_gate is not None:
            execution_context = self.execution_gate.authenticated_context(
                request_id=envelope.request_id,
                caller_id=envelope.caller_id,
                session_id=envelope.session_id,
            )

        if envelope.operation == "status":
            if envelope.payload or self.status_provider is None:
                lifecycle.transition(RequestState.FAILED)
                return self._failure(envelope, lifecycle, "malformed_payload")
            return IPCResponseEnvelope.success(
                envelope.request_id, dict(self.status_provider())
            )
        if envelope.operation == "activity":
            try:
                limit = _activity_limit(envelope.payload)
                if self.activity_reader is None:
                    raise ValueError("activity is unavailable")
                events = [dict(item) for item in self.activity_reader(limit)]
            except (OSError, TypeError, ValueError):
                lifecycle.transition(RequestState.FAILED)
                return self._failure(envelope, lifecycle, "activity_unavailable")
            return IPCResponseEnvelope.success(envelope.request_id, {"events": events})
        if envelope.operation in {
            "voice-status",
            "voice-start",
            "voice-stop",
            "voice-events",
            "wake-start",
            "wake-stop",
            "wake-test-start",
            "wake-phrase-set",
        }:
            if self.voice_handler is None:
                lifecycle.transition(RequestState.FAILED)
                return self._failure(envelope, lifecycle, "voice_unavailable")
            try:
                result = dict(
                    self.voice_handler(
                        envelope.operation,
                        envelope.payload,
                        envelope.caller_id,
                        envelope.session_id,
                    )
                )
            except Exception as error:
                from .voice import VoiceError

                lifecycle.transition(RequestState.FAILED)
                code = (
                    error.code if isinstance(error, VoiceError) else "voice_unavailable"
                )
                return self._failure(envelope, lifecycle, code)
            return IPCResponseEnvelope.success(envelope.request_id, result)
        if envelope.operation.startswith("automation-"):
            if self.automation_handler is None:
                lifecycle.transition(RequestState.FAILED)
                return self._failure(envelope, lifecycle, "automation_unavailable")
            return self.automation_handler(envelope)
        if envelope.operation == "assistant-request":
            if self.assistant_handler is None:
                lifecycle.transition(RequestState.FAILED)
                return self._failure(envelope, lifecycle, "assistant_unavailable")
            return self.assistant_handler(envelope)
        if envelope.operation not in {"prepare", "execute"}:
            lifecycle.transition(RequestState.FAILED)
            return self._failure(envelope, lifecycle, "unsupported_operation")
        try:
            request = self.request_decoder(envelope)
            if not isinstance(request, ControlRequest):
                raise TypeError("decoder did not return a ControlRequest")
            approval_id = _approval_id(envelope.payload)
        except (TypeError, ValueError):
            lifecycle.transition(RequestState.FAILED)
            return self._failure(envelope, lifecycle, "malformed_payload")

        if (
            request.action.caller != envelope.caller_id
            or request.action.session != envelope.session_id
        ):
            lifecycle.transition(RequestState.DENIED)
            return self._failure(envelope, lifecycle, "identity_mismatch")

        if envelope.operation == "execute":
            if self.execution_gate is None or execution_context is None:
                lifecycle.transition(RequestState.FAILED)
                return self._failure(envelope, lifecycle, "execution_unavailable")
            executed = self.execution_gate.execute(execution_context, request)
            result = {
                "state": executed.status.value,
                "history": [state.value for state in executed.lifecycle],
                "request_id": executed.request_id,
                "caller_id": executed.caller_id,
                "session_id": executed.session_id,
                "output": executed.output,
            }
            if executed.status.value == "completed":
                return IPCResponseEnvelope.success(envelope.request_id, result)
            return IPCResponseEnvelope.failure(
                envelope.request_id,
                (
                    executed.error_category.value
                    if executed.error_category is not None
                    else "execution_failed"
                ),
                f"request ended in {executed.status.value}",
            )

        try:
            checked = self.control_plane.check_policy(request)
        except ControlPlaneError:
            lifecycle.transition(RequestState.DENIED)
            return self._failure(envelope, lifecycle, "policy_denied")
        except (RuntimeError, ValueError):
            lifecycle.transition(RequestState.FAILED)
            return self._failure(envelope, lifecycle, "preparation_failed")
        lifecycle.transition(RequestState.POLICY_CHECKED)

        outcome = checked.permission.outcome
        if outcome is DecisionOutcome.MUST_BE_DENIED:
            lifecycle.transition(RequestState.DENIED)
            return self._failure(envelope, lifecycle, "policy_denied")

        if outcome is DecisionOutcome.MAY_PROCEED:
            lifecycle.transition(RequestState.APPROVED)
            try:
                prepared = self.control_plane.prepare(request)
            except RoutingError:
                lifecycle.transition(RequestState.FAILED)
                return self._failure(envelope, lifecycle, "preparation_failed")
            except ControlPlaneError:
                lifecycle.transition(RequestState.DENIED)
                return self._failure(envelope, lifecycle, "policy_denied")
            if (
                prepared.permission.outcome is not DecisionOutcome.MAY_PROCEED
                or prepared.invocation is None
            ):
                lifecycle.transition(RequestState.DENIED)
                return self._failure(envelope, lifecycle, "policy_denied")
            lifecycle.transition(RequestState.PREPARED)
            if self.execution_gate is not None and execution_context is not None:
                try:
                    self.execution_gate.register_prepared(
                        execution_context,
                        request=request,
                        result=prepared,
                        lifecycle=lifecycle,
                    )
                except (RuntimeError, ValueError):
                    lifecycle.transition(RequestState.DENIED)
                    return self._failure(envelope, lifecycle, "preparation_failed")
            return _prepared_response(envelope, lifecycle, prepared.invocation)

        lifecycle.transition(RequestState.AWAITING_APPROVAL)
        if approval_id is None:
            if self.consent_coordinator is not None and execution_context is not None:
                try:
                    pending = self.consent_coordinator.register(
                        request_id=envelope.request_id,
                        request=request,
                        checked=checked,
                        lifecycle=lifecycle,
                        execution_context=execution_context,
                    )
                except (RuntimeError, ValueError):
                    lifecycle.transition(RequestState.DENIED)
                    return self._failure(envelope, lifecycle, "consent_unavailable")
                return IPCResponseEnvelope.success(
                    envelope.request_id,
                    {
                        "state": lifecycle.state.value,
                        "history": [state.value for state in lifecycle.history],
                        "consent": pending.presentation(
                            now=self.consent_coordinator.now()
                        ),
                    },
                )
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
            return self._failure(envelope, lifecycle, "approval_denied")
        lifecycle.transition(RequestState.APPROVED)
        try:
            prepared = self.control_plane.prepare_confirmed(request, consumed)
        except RoutingError:
            lifecycle.transition(RequestState.FAILED)
            return self._failure(envelope, lifecycle, "preparation_failed")
        except ControlPlaneError:
            lifecycle.transition(RequestState.DENIED)
            return self._failure(envelope, lifecycle, "policy_denied")
        if prepared.invocation is None:
            lifecycle.transition(RequestState.FAILED)
            return self._failure(envelope, lifecycle, "preparation_failed")
        lifecycle.transition(RequestState.PREPARED)
        if self.execution_gate is not None and execution_context is not None:
            try:
                self.execution_gate.register_prepared(
                    execution_context,
                    request=request,
                    result=prepared,
                    lifecycle=lifecycle,
                    approval_id=consumed.approval_id,
                )
            except (RuntimeError, ValueError):
                lifecycle.transition(RequestState.DENIED)
                return self._failure(envelope, lifecycle, "preparation_failed")
        return _prepared_response(envelope, lifecycle, prepared.invocation)

    def _failure(
        self,
        envelope: IPCRequestEnvelope,
        lifecycle: RequestLifecycle,
        code: str,
    ) -> IPCResponseEnvelope:
        if self.execution_gate is not None:
            self.execution_gate.record_boundary_denial(
                request_id=envelope.request_id,
                caller_id=envelope.caller_id,
                session_id=envelope.session_id,
                error_category=code,
            )
        return _failure(envelope, lifecycle, code)


def _approval_id(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("approval_id")
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("approval_id must be bounded text")
    return value


def _activity_limit(payload: Mapping[str, Any]) -> int:
    if set(payload).difference({"limit"}):
        raise ValueError("activity payload has unknown fields")
    value = payload.get("limit", 50)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("activity limit must be between 1 and 100")
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
                "route_candidate_id": invocation.route_candidate_id,
                "fallback_candidate_ids": list(invocation.fallback_candidate_ids),
            },
        },
    )
