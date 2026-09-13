"""Short-lived, exact-action, one-time approvals for JL requests."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum


class ApprovalState(StrEnum):
    ISSUED = "issued"
    AVAILABLE = "available"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ApprovalError(RuntimeError):
    """Raised when an approval cannot complete a legal secure transition."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    approval_id: str
    binding_fingerprint: str
    caller_id: str
    session_id: str
    issued_at: float
    expires_at: float
    state: ApprovalState
    consumed_at: float | None = None


class OneTimeApprovalStore:
    """Concurrency-safe in-memory approval lifecycle.

    Issuance and activation belong to a trusted consent surface. Untrusted IPC
    callers receive only the ability to present an already-available approval.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._records: dict[str, ApprovalRecord] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        *,
        binding_fingerprint: str,
        caller_id: str,
        session_id: str,
        ttl_seconds: float,
    ) -> ApprovalRecord:
        _validate_fingerprint(binding_fingerprint)
        _validate_identity(caller_id, "caller_id")
        _validate_identity(session_id, "session_id")
        if not 0 < ttl_seconds <= 300:
            raise ApprovalError("invalid_ttl", "approval TTL must be within 5 minutes")
        now = self._clock()
        record = ApprovalRecord(
            approval_id=secrets.token_urlsafe(32),
            binding_fingerprint=binding_fingerprint,
            caller_id=caller_id,
            session_id=session_id,
            issued_at=now,
            expires_at=now + ttl_seconds,
            state=ApprovalState.ISSUED,
        )
        with self._lock:
            self._records[record.approval_id] = record
        return record

    def make_available(self, approval_id: str) -> ApprovalRecord:
        with self._lock:
            record = self._current(approval_id)
            if record.state is not ApprovalState.ISSUED:
                raise ApprovalError(
                    "illegal_approval_transition",
                    f"approval cannot become available from {record.state.value}",
                )
            available = replace(record, state=ApprovalState.AVAILABLE)
            self._records[approval_id] = available
            return available

    def consume(
        self,
        approval_id: str,
        *,
        binding_fingerprint: str,
        caller_id: str,
        session_id: str,
    ) -> ApprovalRecord:
        with self._lock:
            record = self._current(approval_id)
            if record.state is ApprovalState.CONSUMED:
                raise ApprovalError(
                    "approval_replayed", "approval was already consumed"
                )
            if record.state is not ApprovalState.AVAILABLE:
                raise ApprovalError(
                    "approval_unavailable",
                    f"approval is {record.state.value}",
                )
            if (
                record.binding_fingerprint != binding_fingerprint
                or record.caller_id != caller_id
                or record.session_id != session_id
            ):
                raise ApprovalError(
                    "approval_mismatch", "approval does not match the exact request"
                )
            consumed = replace(
                record,
                state=ApprovalState.CONSUMED,
                consumed_at=self._clock(),
            )
            self._records[approval_id] = consumed
            return consumed

    def revoke(self, approval_id: str) -> ApprovalRecord:
        with self._lock:
            record = self._current(approval_id)
            if record.state in {
                ApprovalState.CONSUMED,
                ApprovalState.EXPIRED,
                ApprovalState.REVOKED,
            }:
                raise ApprovalError(
                    "illegal_approval_transition",
                    f"approval cannot be revoked from {record.state.value}",
                )
            revoked = replace(record, state=ApprovalState.REVOKED)
            self._records[approval_id] = revoked
            return revoked

    def get(self, approval_id: str) -> ApprovalRecord:
        with self._lock:
            return self._current(approval_id)

    def _current(self, approval_id: str) -> ApprovalRecord:
        try:
            record = self._records[approval_id]
        except KeyError as error:
            raise ApprovalError(
                "approval_not_found", "approval was not found"
            ) from error
        if (
            record.state in {ApprovalState.ISSUED, ApprovalState.AVAILABLE}
            and self._clock() >= record.expires_at
        ):
            record = replace(record, state=ApprovalState.EXPIRED)
            self._records[approval_id] = record
        return record


def _validate_fingerprint(value: str) -> None:
    invalid_character = any(
        character not in "0123456789abcdef" for character in value
    )
    if len(value) != 64 or invalid_character:
        raise ApprovalError(
            "invalid_fingerprint", "approval requires one exact SHA-256 fingerprint"
        )


def _validate_identity(value: str, field: str) -> None:
    if not value or len(value) > 256:
        raise ApprovalError(
            "invalid_identity", f"{field} must be non-empty bounded text"
        )
