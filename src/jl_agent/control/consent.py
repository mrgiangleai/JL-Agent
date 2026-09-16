"""Trusted native consent coordination outside the normal IPC endpoint."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .approvals import ApprovalError, OneTimeApprovalStore
from .control_plane import (
    ControlPathResult,
    ControlPlaneError,
    ControlRequest,
    JLControlPlane,
)
from .ipc import IPCRequestEnvelope, IPCResponseEnvelope
from .permissions import ActionClass, DecisionOutcome
from .request_state import RequestLifecycle, RequestState
from .router import RoutingError

if TYPE_CHECKING:
    from .automation_management import AutomationConsentRequestHandler
    from .execution import AuthenticatedExecutionContext, ExecutionGate


class ConsentError(RuntimeError):
    """Raised when a trusted consent transition must fail closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ConsentDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ConsentState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class PendingConsent:
    consent_id: str
    request_id: str
    caller_id: str
    session_id: str
    nonce: str
    binding_fingerprint: str
    capability_id: str
    action: str
    action_classes: tuple[str, ...]
    target_summary: str
    risk_level: str
    request: ControlRequest
    checked: ControlPathResult
    lifecycle: RequestLifecycle
    execution_context: AuthenticatedExecutionContext
    created_at: float
    expires_at: float
    state: ConsentState = ConsentState.PENDING

    def presentation(self, *, now: float) -> dict[str, object]:
        return {
            "consent_id": self.consent_id,
            "request_id": self.request_id,
            "caller_id": self.caller_id,
            "session_id": self.session_id,
            "nonce": self.nonce,
            "capability_id": self.capability_id,
            "action": self.action,
            "action_classes": list(self.action_classes),
            "target_summary": self.target_summary,
            "risk_level": self.risk_level,
            "expires_in_seconds": max(0, int(self.expires_at - now)),
        }


class ConsentCoordinator:
    """Own bounded exact pending consent records created by runtime policy."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 30.0,
        max_pending_records: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0 < ttl_seconds <= 300:
            raise ValueError("consent TTL must be within 5 minutes")
        if max_pending_records < 1:
            raise ValueError("pending consent bound must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_pending_records = max_pending_records
        self._clock = clock
        self._records: dict[str, PendingConsent] = {}
        self._request_ids: set[str] = set()
        self._lock = threading.Lock()

    def now(self) -> float:
        return self._clock()

    def register(
        self,
        *,
        request_id: str,
        request: ControlRequest,
        checked: ControlPathResult,
        lifecycle: RequestLifecycle,
        execution_context: AuthenticatedExecutionContext,
    ) -> PendingConsent:
        if checked.permission.outcome is not DecisionOutcome.REQUIRES_CONFIRMATION:
            raise ConsentError(
                "consent_not_required", "only confirmation policy may create consent"
            )
        if lifecycle.state is not RequestState.AWAITING_APPROVAL:
            raise ConsentError(
                "invalid_consent_state", "request is not awaiting approval"
            )
        now = self._clock()
        with self._lock:
            self._expire_locked(now)
            terminal = sorted(
                (
                    record
                    for record in self._records.values()
                    if record.state is not ConsentState.PENDING
                ),
                key=lambda item: item.created_at,
            )
            while len(self._records) >= self.max_pending_records and terminal:
                oldest = terminal.pop(0)
                self._records.pop(oldest.consent_id, None)
                self._request_ids.discard(oldest.request_id)
            if request_id in self._request_ids:
                raise ConsentError(
                    "duplicate_consent", "request already has a consent record"
                )
            if len(self._records) >= self.max_pending_records:
                raise ConsentError(
                    "consent_unavailable", "pending consent bound is exhausted"
                )
            record = PendingConsent(
                consent_id=secrets.token_urlsafe(24),
                request_id=request_id,
                caller_id=request.action.caller,
                session_id=request.action.session,
                nonce=secrets.token_urlsafe(32),
                binding_fingerprint=checked.permission.binding_fingerprint,
                capability_id=checked.capability.id,
                action=_safe_summary(request.action.action, 128),
                action_classes=tuple(
                    item.value for item in checked.permission.action_classes
                ),
                target_summary=_safe_summary(request.action.resolved_target, 512),
                risk_level=_risk_level(checked.permission.action_classes),
                request=request,
                checked=checked,
                lifecycle=lifecycle,
                execution_context=execution_context,
                created_at=now,
                expires_at=now + self.ttl_seconds,
            )
            self._records[record.consent_id] = record
            self._request_ids.add(request_id)
            return record

    def get(
        self,
        consent_id: str,
        *,
        request_id: str,
        caller_id: str,
        session_id: str,
    ) -> PendingConsent:
        now = self._clock()
        with self._lock:
            try:
                record = self._records[consent_id]
            except KeyError as error:
                raise ConsentError(
                    "consent_not_found", "pending consent was not found"
                ) from error
            record = self._current_locked(record, now)
            self._require_pending_locked(record)
            if (
                record.request_id != request_id
                or record.caller_id != caller_id
                or record.session_id != session_id
            ):
                raise ConsentError(
                    "identity_mismatch", "consent identity does not match"
                )
            return record

    def decide(
        self, consent_id: str, decision: ConsentDecision
    ) -> PendingConsent:
        now = self._clock()
        with self._lock:
            try:
                record = self._records[consent_id]
            except KeyError as error:
                raise ConsentError(
                    "consent_not_found", "pending consent was not found"
                ) from error
            record = self._current_locked(record, now)
            self._require_pending_locked(record)
            state = (
                ConsentState.APPROVED
                if decision is ConsentDecision.APPROVE
                else ConsentState.REJECTED
            )
            decided = replace(record, state=state)
            self._records[consent_id] = decided
            return decided

    @staticmethod
    def _require_pending_locked(record: PendingConsent) -> None:
        if record.state is ConsentState.EXPIRED:
            if record.lifecycle.state is RequestState.AWAITING_APPROVAL:
                record.lifecycle.transition(RequestState.DENIED)
            raise ConsentError("consent_expired", "consent has expired")
        if record.state is not ConsentState.PENDING:
            raise ConsentError(
                "consent_replayed", f"consent is {record.state.value}"
            )

    def _expire_locked(self, now: float) -> None:
        for record in tuple(self._records.values()):
            self._current_locked(record, now)

    def _current_locked(
        self, record: PendingConsent, now: float
    ) -> PendingConsent:
        if record.state is ConsentState.PENDING and now >= record.expires_at:
            record = replace(record, state=ConsentState.EXPIRED)
            self._records[record.consent_id] = record
        return record


class ConsentSignatureVerifier(Protocol):
    available: bool
    key_fingerprint: str | None

    def verify(self, message: bytes, signature: str) -> bool: ...


class UnavailableConsentVerifier:
    available = False
    key_fingerprint = None

    def verify(self, message: bytes, signature: str) -> bool:
        return False


class RSAPKCS1v15SHA256Verifier:
    """Verify native Keychain RSA signatures without adding a dependency."""

    available = True

    def __init__(
        self, modulus: int, exponent: int, *, key_fingerprint: str | None = None
    ) -> None:
        if modulus.bit_length() < 2048 or exponent < 3 or exponent % 2 == 0:
            raise ValueError("native consent public key is invalid")
        self.modulus = modulus
        self.exponent = exponent
        self.key_fingerprint = key_fingerprint
        self._size = (modulus.bit_length() + 7) // 8

    @classmethod
    def from_file(cls, path: str | Path) -> RSAPKCS1v15SHA256Verifier:
        key_path = Path(path)
        details = key_path.lstat()
        permissions = stat.S_IMODE(details.st_mode)
        if (
            details.st_uid != os.geteuid()
            or not stat.S_ISREG(details.st_mode)
            or permissions & 0o077
            or permissions & 0o600 != 0o600
            or details.st_size > 16 * 1024
        ):
            raise ValueError("native consent public key must be a private owned file")
        encoded = key_path.read_bytes()
        modulus, exponent = _parse_rsa_public_key(encoded)
        return cls(
            modulus,
            exponent,
            key_fingerprint=hashlib.sha256(encoded).hexdigest(),
        )

    def verify(self, message: bytes, signature: str) -> bool:
        try:
            decoded = base64.b64decode(signature, validate=True)
        except (binascii.Error, ValueError):
            return False
        if len(decoded) != self._size:
            return False
        number = int.from_bytes(decoded, "big")
        if number <= 0 or number >= self.modulus:
            return False
        encoded = pow(number, self.exponent, self.modulus).to_bytes(
            self._size, "big"
        )
        digest_info = bytes.fromhex("3031300d060960864801650304020105000420")
        digest_info += hashlib.sha256(message).digest()
        padding_length = self._size - len(digest_info) - 3
        if padding_length < 8:
            return False
        expected = b"\x00\x01" + (b"\xff" * padding_length) + b"\x00" + digest_info
        return hmac.compare_digest(encoded, expected)


def consent_signature_message(
    record: PendingConsent, decision: ConsentDecision
) -> bytes:
    fields = (
        record.consent_id,
        record.request_id,
        record.caller_id,
        record.session_id,
        decision.value,
        record.nonce,
    )
    message = bytearray(b"jl-agent-consent-v1\0")
    for field in fields:
        encoded = field.encode("utf-8")
        message.extend(len(encoded).to_bytes(8, "big"))
        message.extend(encoded)
    return bytes(message)


class TrustedConsentRequestHandler:
    """Constrained signed consent endpoint; never a generic approval issuer."""

    def __init__(
        self,
        *,
        coordinator: ConsentCoordinator,
        verifier: ConsentSignatureVerifier,
        approvals: OneTimeApprovalStore,
        control_plane: JLControlPlane,
        execution_gate: ExecutionGate,
        automation_handler: AutomationConsentRequestHandler | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.verifier = verifier
        self.approvals = approvals
        self.control_plane = control_plane
        self.execution_gate = execution_gate
        self.automation_handler = automation_handler

    def __call__(self, envelope: IPCRequestEnvelope) -> IPCResponseEnvelope:
        if self.automation_handler is not None:
            response = self.automation_handler(envelope)
            if response is not None:
                return response
        if envelope.operation != "consent-decision":
            return _failure(envelope, "unsupported_operation")
        if not self.verifier.available:
            return _failure(envelope, "consent_unavailable")
        try:
            consent_id, decision, signature = _decode_decision(envelope)
            pending = self.coordinator.get(
                consent_id,
                request_id=envelope.request_id,
                caller_id=envelope.caller_id,
                session_id=envelope.session_id,
            )
        except ConsentError as error:
            if error.code == "consent_expired":
                self.execution_gate.record_boundary_denial(
                    request_id=envelope.request_id,
                    caller_id=envelope.caller_id,
                    session_id=envelope.session_id,
                    error_category=error.code,
                )
            return _failure(envelope, error.code)
        except ValueError:
            return _failure(envelope, "malformed_payload")

        message = consent_signature_message(pending, decision)
        if not self.verifier.verify(message, signature):
            self.execution_gate.record_boundary_denial(
                request_id=envelope.request_id,
                caller_id=envelope.caller_id,
                session_id=envelope.session_id,
                error_category="invalid_consent_signature",
            )
            return _failure(envelope, "invalid_consent_signature")

        try:
            decided = self.coordinator.decide(consent_id, decision)
        except ConsentError as error:
            if error.code == "consent_expired":
                self.execution_gate.record_boundary_denial(
                    request_id=envelope.request_id,
                    caller_id=envelope.caller_id,
                    session_id=envelope.session_id,
                    error_category=error.code,
                )
            return _failure(envelope, error.code)
        if decision is ConsentDecision.REJECT:
            decided.lifecycle.transition(RequestState.DENIED)
            self.execution_gate.record_boundary_denial(
                request_id=envelope.request_id,
                caller_id=envelope.caller_id,
                session_id=envelope.session_id,
                error_category="consent_rejected",
            )
            return IPCResponseEnvelope.success(
                envelope.request_id, {"state": RequestState.DENIED.value}
            )

        try:
            approval = self.approvals.issue(
                binding_fingerprint=decided.binding_fingerprint,
                caller_id=decided.caller_id,
                session_id=decided.session_id,
                ttl_seconds=30.0,
            )
            available = self.approvals.make_available(approval.approval_id)
            consumed = self.approvals.consume(
                available.approval_id,
                binding_fingerprint=decided.binding_fingerprint,
                caller_id=decided.caller_id,
                session_id=decided.session_id,
            )
            decided.lifecycle.transition(RequestState.APPROVED)
            prepared = self.control_plane.prepare_confirmed(decided.request, consumed)
            if prepared.invocation is None:
                raise ControlPlaneError("confirmed request did not prepare")
            decided.lifecycle.transition(RequestState.PREPARED)
            self.execution_gate.register_prepared(
                decided.execution_context,
                request=decided.request,
                result=prepared,
                lifecycle=decided.lifecycle,
                approval_id=consumed.approval_id,
            )
        except (
            ApprovalError,
            ControlPlaneError,
            RoutingError,
            RuntimeError,
            ValueError,
        ):
            if decided.lifecycle.state in {
                RequestState.AWAITING_APPROVAL,
                RequestState.APPROVED,
            }:
                decided.lifecycle.transition(RequestState.DENIED)
            self.execution_gate.record_boundary_denial(
                request_id=envelope.request_id,
                caller_id=envelope.caller_id,
                session_id=envelope.session_id,
                error_category="consent_preparation_failed",
            )
            return _failure(envelope, "consent_preparation_failed")
        return IPCResponseEnvelope.success(
            envelope.request_id,
            {
                "state": RequestState.PREPARED.value,
                "request_id": envelope.request_id,
            },
        )


def _decode_decision(
    envelope: IPCRequestEnvelope,
) -> tuple[str, ConsentDecision, str]:
    payload = dict(envelope.payload)
    if set(payload) != {"consent_id", "decision", "signature"}:
        raise ValueError("consent payload fields do not match the schema")
    consent_id = _bounded_text(payload["consent_id"], 128)
    signature = _bounded_text(payload["signature"], 4096)
    try:
        decision = ConsentDecision(payload["decision"])
    except (TypeError, ValueError) as error:
        raise ValueError("invalid consent decision") from error
    return consent_id, decision, signature


def _failure(
    envelope: IPCRequestEnvelope, code: str
) -> IPCResponseEnvelope:
    return IPCResponseEnvelope.failure(
        envelope.request_id, code, "trusted consent request was denied"
    )


def _safe_summary(value: str, maximum: int) -> str:
    cleaned = " ".join(value.split())
    return cleaned[:maximum]


def _risk_level(classes: tuple[ActionClass, ...]) -> str:
    values = set(classes)
    if values.intersection(
        {
            ActionClass.DESTRUCTIVE,
            ActionClass.FINANCIAL_HIGH_RISK,
            ActionClass.CREDENTIAL_SENSITIVE,
            ActionClass.EXTERNAL_COMMUNICATION,
        }
    ):
        return "high"
    if ActionClass.REVERSIBLE_LOCAL in values:
        return "medium"
    return "low"


def _bounded_text(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("value must be non-empty bounded text")
    return value


def _parse_rsa_public_key(data: bytes) -> tuple[int, int]:
    sequence, end = _der_value(data, 0, 0x30)
    if end != len(data):
        raise ValueError("native consent public key has trailing data")
    try:
        modulus_bytes, offset = _der_value(sequence, 0, 0x02)
        exponent_bytes, final = _der_value(sequence, offset, 0x02)
        if final == len(sequence):
            return (
                int.from_bytes(modulus_bytes, "big"),
                int.from_bytes(exponent_bytes, "big"),
            )
    except ValueError:
        pass

    _, offset = _der_value(sequence, 0, 0x30)
    bit_string, final = _der_value(sequence, offset, 0x03)
    if final != len(sequence) or not bit_string or bit_string[0] != 0:
        raise ValueError("native consent public key wrapper is invalid")
    nested, nested_end = _der_value(bit_string[1:], 0, 0x30)
    if nested_end != len(bit_string) - 1:
        raise ValueError("native consent public key wrapper has trailing data")
    modulus_bytes, offset = _der_value(nested, 0, 0x02)
    exponent_bytes, final = _der_value(nested, offset, 0x02)
    if final != len(nested):
        raise ValueError("native consent public key is malformed")
    return int.from_bytes(modulus_bytes, "big"), int.from_bytes(
        exponent_bytes, "big"
    )


def _der_value(data: bytes, offset: int, expected_tag: int) -> tuple[bytes, int]:
    if offset >= len(data) or data[offset] != expected_tag:
        raise ValueError("native consent public key DER tag is invalid")
    offset += 1
    if offset >= len(data):
        raise ValueError("native consent public key DER length is missing")
    first = data[offset]
    offset += 1
    if first < 0x80:
        length = first
    else:
        length_bytes = first & 0x7F
        if length_bytes == 0 or length_bytes > 4 or offset + length_bytes > len(data):
            raise ValueError("native consent public key DER length is invalid")
        length = int.from_bytes(data[offset : offset + length_bytes], "big")
        offset += length_bytes
    end = offset + length
    if end > len(data):
        raise ValueError("native consent public key DER value is truncated")
    return data[offset:end], end
