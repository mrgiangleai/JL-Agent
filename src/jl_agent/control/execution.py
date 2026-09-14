"""Final revalidation gate and single-use execution lifecycle."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .approvals import (
    ApprovalError,
    ApprovalRecord,
    ApprovalState,
    OneTimeApprovalStore,
)
from .audit import AuditLedger
from .control_plane import (
    ControlPathResult,
    ControlPlaneError,
    ControlRequest,
    JLControlPlane,
)
from .execution_adapter import (
    ExecutionErrorCategory,
    HermesExecutionAdapter,
    HermesExecutionStatus,
)
from .permissions import DecisionOutcome
from .request_state import RequestLifecycle, RequestState
from .router import RoutingError


@dataclass(frozen=True, slots=True)
class AuthenticatedExecutionContext:
    request_id: str
    caller_id: str
    session_id: str
    authority: object


@dataclass(slots=True)
class PreparedExecution:
    request: ControlRequest
    result: ControlPathResult
    approval_id: str | None
    lifecycle: RequestLifecycle
    created_at: float
    expires_at: float


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    status: HermesExecutionStatus
    lifecycle: tuple[RequestState, ...]
    request_id: str
    caller_id: str
    session_id: str
    output: str | None = None
    error_category: ExecutionErrorCategory | None = None


class ExecutionGate:
    """Revalidate prepared state exactly once immediately before Hermes."""

    def __init__(
        self,
        *,
        control_plane: JLControlPlane,
        approvals: OneTimeApprovalStore,
        adapter: HermesExecutionAdapter,
        audit: AuditLedger,
        preparation_ttl_seconds: float = 30.0,
        max_prepared_records: int = 1024,
        clock: Callable[[], float] = time.monotonic,
        target_validator: Callable[[ControlRequest], None] | None = None,
    ) -> None:
        if not 0 < preparation_ttl_seconds <= 300:
            raise ValueError("preparation TTL must be within 5 minutes")
        if max_prepared_records < 1:
            raise ValueError("prepared record bound must be positive")
        self.control_plane = control_plane
        self.approvals = approvals
        self.adapter = adapter
        self.audit = audit
        self.preparation_ttl_seconds = preparation_ttl_seconds
        self.max_prepared_records = max_prepared_records
        self._clock = clock
        self.target_validator = target_validator
        self._authority = object()
        self._prepared: dict[str, PreparedExecution] = {}
        self._lock = threading.Lock()

    def authenticated_context(
        self, *, request_id: str, caller_id: str, session_id: str
    ) -> AuthenticatedExecutionContext:
        """Create the handoff used by the authenticated IPC handler."""
        return AuthenticatedExecutionContext(
            request_id, caller_id, session_id, self._authority
        )

    def register_prepared(
        self,
        context: AuthenticatedExecutionContext,
        *,
        request: ControlRequest,
        result: ControlPathResult,
        lifecycle: RequestLifecycle,
        approval_id: str | None = None,
    ) -> None:
        self._validate_context(context)
        invocation = result.invocation
        if lifecycle.state is not RequestState.PREPARED or invocation is None:
            raise ValueError("only a fully prepared request can be registered")
        if (
            request.action.caller != context.caller_id
            or request.action.session != context.session_id
            or invocation.caller_id != context.caller_id
            or invocation.session_id != context.session_id
        ):
            raise ValueError("prepared identity does not match authenticated context")
        if result.permission.outcome is DecisionOutcome.REQUIRES_CONFIRMATION:
            if approval_id is None:
                raise ValueError("confirmed preparation requires approval identity")
            self._require_matching_approval(
                context, invocation.binding_fingerprint, approval_id
            )
        elif approval_id is not None:
            raise ValueError("direct allow preparation cannot carry an approval")

        now = self._clock()
        prepared = PreparedExecution(
            request=request,
            result=result,
            approval_id=approval_id,
            lifecycle=lifecycle,
            created_at=now,
            expires_at=now + self.preparation_ttl_seconds,
        )
        with self._lock:
            expired = [
                request_id
                for request_id, item in self._prepared.items()
                if now >= item.expires_at
            ]
            for request_id in expired:
                self._prepared.pop(request_id)
            if context.request_id in self._prepared:
                raise ValueError("request ID already has prepared state")
            if len(self._prepared) >= self.max_prepared_records:
                raise ValueError("prepared record bound is exhausted")
            self._prepared[context.request_id] = prepared
        self._record("execution_prepared", context, prepared)

    def execute(
        self,
        context: AuthenticatedExecutionContext,
        current_request: ControlRequest,
    ) -> ExecutionResult:
        try:
            self._validate_context(context)
        except ValueError:
            self._record_unprepared_denial(
                context, ExecutionErrorCategory.POLICY_DENIED
            )
            return ExecutionResult(
                HermesExecutionStatus.DENIED,
                (RequestState.RECEIVED, RequestState.DENIED),
                context.request_id,
                context.caller_id,
                context.session_id,
                error_category=ExecutionErrorCategory.POLICY_DENIED,
            )
        with self._lock:
            prepared = self._prepared.get(context.request_id)
            if prepared is None:
                self._record_unprepared_denial(
                    context, ExecutionErrorCategory.STALE_PREPARATION
                )
                return ExecutionResult(
                    HermesExecutionStatus.DENIED,
                    (RequestState.RECEIVED, RequestState.DENIED),
                    context.request_id,
                    context.caller_id,
                    context.session_id,
                    error_category=ExecutionErrorCategory.STALE_PREPARATION,
                )
            if prepared.lifecycle.state is not RequestState.PREPARED:
                self._record(
                    "execution_denied",
                    context,
                    prepared,
                    error=ExecutionErrorCategory.DUPLICATE_EXECUTION,
                )
                return ExecutionResult(
                    HermesExecutionStatus.DENIED,
                    tuple(prepared.lifecycle.history),
                    context.request_id,
                    context.caller_id,
                    context.session_id,
                    error_category=ExecutionErrorCategory.DUPLICATE_EXECUTION,
                )
            denial = self._revalidation_error(context, prepared, current_request)
            if denial is not None:
                prepared.lifecycle.transition(RequestState.DENIED)
                self._record("execution_denied", context, prepared, error=denial)
                return ExecutionResult(
                    HermesExecutionStatus.DENIED,
                    tuple(prepared.lifecycle.history),
                    context.request_id,
                    context.caller_id,
                    context.session_id,
                    error_category=denial,
                )
            prepared.lifecycle.transition(RequestState.EXECUTING)
            started_at = self._clock()
            self._record("execution_started", context, prepared)

        invocation = prepared.result.invocation
        assert invocation is not None
        command = self.adapter._issue_command(invocation, context.request_id)
        result = self.adapter._execute(command)
        duration_ms = max(0, int((self._clock() - started_at) * 1000))
        with self._lock:
            if result.status is HermesExecutionStatus.COMPLETED:
                prepared.lifecycle.transition(RequestState.COMPLETED)
                event = "execution_completed"
            elif result.status is HermesExecutionStatus.DENIED:
                prepared.lifecycle.transition(RequestState.DENIED)
                event = "execution_denied"
            else:
                prepared.lifecycle.transition(RequestState.FAILED)
                event = "execution_failed"
            self._record(
                event,
                context,
                prepared,
                error=result.error_category,
                duration_ms=duration_ms,
            )
            return ExecutionResult(
                result.status,
                tuple(prepared.lifecycle.history),
                context.request_id,
                context.caller_id,
                context.session_id,
                output=result.output,
                error_category=result.error_category,
            )

    def record_boundary_denial(
        self,
        *,
        request_id: str,
        caller_id: str,
        session_id: str,
        error_category: str,
    ) -> None:
        """Record metadata-only denials that occur before preparation."""
        self.audit.record(
            "request_denied",
            request_id=request_id,
            caller_id=caller_id,
            session_id=session_id,
            error_category=error_category,
        )

    def _record_unprepared_denial(
        self,
        context: AuthenticatedExecutionContext,
        error: ExecutionErrorCategory,
    ) -> None:
        self.audit.record(
            "execution_denied",
            request_id=context.request_id,
            caller_id=context.caller_id,
            session_id=context.session_id,
            error_category=error.value,
        )

    def _revalidation_error(
        self,
        context: AuthenticatedExecutionContext,
        prepared: PreparedExecution,
        current_request: ControlRequest,
    ) -> ExecutionErrorCategory | None:
        invocation = prepared.result.invocation
        assert invocation is not None
        if self._clock() >= prepared.expires_at:
            return ExecutionErrorCategory.STALE_PREPARATION
        if (
            current_request.action.caller != context.caller_id
            or current_request.action.session != context.session_id
            or prepared.request.action.caller != context.caller_id
            or prepared.request.action.session != context.session_id
        ):
            return ExecutionErrorCategory.POLICY_DENIED
        if prepared.result.permission.outcome is DecisionOutcome.REQUIRES_CONFIRMATION:
            if prepared.approval_id is None:
                return ExecutionErrorCategory.APPROVAL_INVALID
            try:
                self._require_matching_approval(
                    context, invocation.binding_fingerprint, prepared.approval_id
                )
            except ValueError:
                return ExecutionErrorCategory.APPROVAL_INVALID
        try:
            fresh = self.control_plane.revalidate_route(current_request)
        except RoutingError:
            return ExecutionErrorCategory.ROUTING_MISMATCH
        except ControlPlaneError:
            return ExecutionErrorCategory.POLICY_DENIED
        except (RuntimeError, ValueError):
            return ExecutionErrorCategory.INVALID_REQUEST
        if fresh.permission.outcome is not prepared.result.permission.outcome:
            return ExecutionErrorCategory.POLICY_DENIED
        if fresh.permission.binding_fingerprint != invocation.binding_fingerprint:
            return ExecutionErrorCategory.STALE_PREPARATION
        if fresh.invocation != invocation:
            return ExecutionErrorCategory.ROUTING_MISMATCH
        if self.target_validator is not None:
            try:
                self.target_validator(current_request)
            except (RuntimeError, ValueError):
                return ExecutionErrorCategory.TARGET_CONTEXT_CHANGED
        return None

    def _require_matching_approval(
        self,
        context: AuthenticatedExecutionContext,
        fingerprint: str,
        approval_id: str,
    ) -> ApprovalRecord:
        try:
            approval = self.approvals.get(approval_id)
        except ApprovalError as error:
            raise ValueError("approval is unavailable") from error
        if (
            approval.state is not ApprovalState.CONSUMED
            or self._clock() >= approval.expires_at
            or approval.binding_fingerprint != fingerprint
            or approval.caller_id != context.caller_id
            or approval.session_id != context.session_id
        ):
            raise ValueError("approval is not validly consumed for this request")
        return approval

    def _validate_context(self, context: AuthenticatedExecutionContext) -> None:
        if context.authority is not self._authority:
            raise ValueError(
                "execution context did not come from authentication boundary"
            )

    def _record(
        self,
        event: str,
        context: AuthenticatedExecutionContext,
        prepared: PreparedExecution,
        *,
        error: ExecutionErrorCategory | None = None,
        duration_ms: int | None = None,
    ) -> None:
        invocation = prepared.result.invocation
        assert invocation is not None
        self.audit.record(
            event,
            request_id=context.request_id,
            caller_id=context.caller_id,
            session_id=context.session_id,
            capability_id=invocation.capability_id,
            capability_version=invocation.capability_version,
            action_class=tuple(
                item.value for item in prepared.result.permission.action_classes
            ),
            policy_decision=prepared.result.permission.outcome.value,
            fingerprint_reference=invocation.binding_fingerprint,
            approval_id_reference=(
                hashlib.sha256(prepared.approval_id.encode("utf-8")).hexdigest()
                if prepared.approval_id is not None
                else ""
            ),
            approval_consumed=prepared.approval_id is not None,
            provider=invocation.provider,
            model=invocation.model,
            error_category=error.value if error else "",
            duration_ms=duration_ms,
        )
