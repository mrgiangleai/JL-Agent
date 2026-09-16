"""Authenticated management surface for JL-owned Hermes reminder jobs."""

from __future__ import annotations

import importlib
import os
import secrets
import stat
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from .approvals import ApprovalError, OneTimeApprovalStore
from .automation import SCRIPT_NAME, AutomationAuthority, AutomationDenied
from .consent import ConsentDecision, ConsentError, ConsentSignatureVerifier
from .ipc import IPCRequestEnvelope, IPCResponseEnvelope


class AutomationConsentState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class PendingAutomationConsent:
    consent_id: str
    request_id: str
    caller_id: str
    session_id: str
    nonce: str
    binding_fingerprint: str
    job_id: str
    job_name: str
    schedule: str
    until: float
    created_at: float
    expires_at: float
    state: AutomationConsentState = AutomationConsentState.PENDING

    def presentation(self, *, now: float) -> dict[str, object]:
        return {
            "consent_id": self.consent_id,
            "request_id": self.request_id,
            "caller_id": self.caller_id,
            "session_id": self.session_id,
            "nonce": self.nonce,
            "capability_id": "core.jl.automation.reminder",
            "action": f"activate reminder {self.job_name}",
            "action_classes": ["local-automation"],
            "target_summary": self.schedule,
            "risk_level": "low",
            "expires_in_seconds": max(0, int(self.expires_at - now)),
            "job_id": self.job_id,
            "valid_until": self.until,
        }


class AutomationConsentCoordinator:
    def __init__(
        self,
        *,
        ttl_seconds: float = 30.0,
        max_pending_records: int = 128,
        clock: Any = time.monotonic,
    ) -> None:
        if not 0 < ttl_seconds <= 300 or max_pending_records < 1:
            raise ValueError("invalid automation consent bounds")
        self.ttl_seconds = ttl_seconds
        self.max_pending_records = max_pending_records
        self._clock = clock
        self._records: dict[str, PendingAutomationConsent] = {}
        self._request_ids: set[str] = set()
        self._lock = threading.Lock()

    def now(self) -> float:
        return float(self._clock())

    def register(
        self,
        *,
        request_id: str,
        caller_id: str,
        session_id: str,
        job: dict[str, Any],
        binding_fingerprint: str,
        until: float,
    ) -> PendingAutomationConsent:
        now = self.now()
        with self._lock:
            self._expire_locked(now)
            terminal = sorted(
                (
                    item
                    for item in self._records.values()
                    if item.state is not AutomationConsentState.PENDING
                ),
                key=lambda item: item.created_at,
            )
            while len(self._records) >= self.max_pending_records and terminal:
                oldest = terminal.pop(0)
                self._records.pop(oldest.consent_id, None)
                self._request_ids.discard(oldest.request_id)
            if request_id in self._request_ids:
                raise ConsentError("duplicate_consent", "request already has consent")
            if len(self._records) >= self.max_pending_records:
                raise ConsentError("consent_unavailable", "automation consent is full")
            record = PendingAutomationConsent(
                consent_id=secrets.token_urlsafe(24),
                request_id=request_id,
                caller_id=caller_id,
                session_id=session_id,
                nonce=secrets.token_urlsafe(32),
                binding_fingerprint=binding_fingerprint,
                job_id=str(job["id"]),
                job_name=_summary(str(job.get("name") or job["id"]), 128),
                schedule=_summary(str(job.get("schedule_display") or ""), 256),
                until=until,
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
    ) -> PendingAutomationConsent:
        now = self.now()
        with self._lock:
            try:
                record = self._records[consent_id]
            except KeyError as error:
                raise ConsentError("consent_not_found", "consent not found") from error
            record = self._current_locked(record, now)
            self._require_pending(record)
            if (
                record.request_id != request_id
                or record.caller_id != caller_id
                or record.session_id != session_id
            ):
                raise ConsentError("identity_mismatch", "automation consent mismatch")
            return record

    def decide(
        self, consent_id: str, decision: ConsentDecision
    ) -> PendingAutomationConsent:
        now = self.now()
        with self._lock:
            try:
                record = self._records[consent_id]
            except KeyError as error:
                raise ConsentError("consent_not_found", "consent not found") from error
            record = self._current_locked(record, now)
            self._require_pending(record)
            state = (
                AutomationConsentState.APPROVED
                if decision is ConsentDecision.APPROVE
                else AutomationConsentState.REJECTED
            )
            decided = replace(record, state=state)
            self._records[consent_id] = decided
            return decided

    def _expire_locked(self, now: float) -> None:
        for record in tuple(self._records.values()):
            self._current_locked(record, now)

    def _current_locked(
        self, record: PendingAutomationConsent, now: float
    ) -> PendingAutomationConsent:
        if record.state is AutomationConsentState.PENDING and now >= record.expires_at:
            record = replace(record, state=AutomationConsentState.EXPIRED)
            self._records[record.consent_id] = record
        return record

    @staticmethod
    def _require_pending(record: PendingAutomationConsent) -> None:
        if record.state is AutomationConsentState.EXPIRED:
            raise ConsentError("consent_expired", "automation consent expired")
        if record.state is not AutomationConsentState.PENDING:
            raise ConsentError("consent_replayed", "automation consent already decided")


class AutomationManager:
    """Thin owner-bound adapter around Hermes cron jobs; never schedules ticks."""

    def __init__(
        self,
        *,
        upstream: Path,
        home: Path,
        approvals: OneTimeApprovalStore,
        clock: Any = time.time,
    ) -> None:
        self.upstream = upstream.resolve()
        self.home = home.resolve()
        self.approvals = approvals
        self.clock = clock
        self.consent = AutomationConsentCoordinator()
        self._lock = threading.RLock()
        self._imports_ready = False

    def status(self, *, scheduler_enabled: bool) -> dict[str, object]:
        stopped = False
        storage_ready = False
        try:
            with self._authority_scope() as authority:
                stopped = authority.stopped()
            storage_ready = True
        except (AutomationDenied, OSError, RuntimeError):
            stopped = True
        return {
            "available": storage_ready,
            "scheduler_enabled": bool(scheduler_enabled),
            "stopped": stopped,
            "profile_home": str(self.home),
            "mode": "fixed-local-no-agent-reminders",
        }

    def list_jobs(self) -> dict[str, object]:
        jobs, _ = self._cron_modules()
        with self._cron_scope(jobs):
            records = [
                self._job_summary(job)
                for job in jobs.list_jobs(include_disabled=True)
            ]
        return {"jobs": records}

    def history(self, job_id: str | None, limit: int) -> dict[str, object]:
        jobs, executions = self._cron_modules()
        with self._cron_scope(jobs):
            rows = executions.list_executions(job_id=job_id, limit=limit)
        return {"executions": [self._execution_summary(item) for item in rows]}

    def create(self, payload: dict[str, Any]) -> dict[str, object]:
        name = _bounded(payload.get("name"), "name", 128)
        schedule = _bounded(payload.get("schedule"), "schedule", 256)
        note = _optional_text(payload.get("note", ""), "note", 4096) or ""
        jobs, _ = self._cron_modules()
        with self._cron_scope(jobs):
            job = jobs.create_job(
                prompt=note or name,
                schedule=schedule,
                name=name,
                script=SCRIPT_NAME,
                no_agent=True,
                deliver="local",
                failure_deliver="local",
                paused=True,
                paused_reason="Created paused; awaiting exact JL activation.",
        )
        # Validate against the JL execution policy shape immediately.
        with self._authority_scope() as authority:
            authority.binding(job, "validation", "validation", self.clock() + 60)
        return {"job": self._job_summary(job)}

    def pause(self, job_id: str, *, owner: str, session: str) -> dict[str, object]:
        jobs, _ = self._cron_modules()
        with self._cron_scope(jobs):
            job = jobs.get_job(job_id)
            if job is None:
                raise AutomationDenied("job_not_found")
            with self._authority_scope() as authority:
                revoked = authority.revoke_if_owner(
                    str(job["id"]), owner=owner, session=session
                )
            if revoked is None and job.get("enabled"):
                raise AutomationDenied("grant_unavailable")
            paused = jobs.pause_job(str(job["id"]), reason="paused_by_native_ui")
        if paused is None:
            raise AutomationDenied("job_not_found")
        return {"job": self._job_summary(paused), "inflight": int(revoked or 0)}

    def remove(self, job_id: str, *, owner: str, session: str) -> dict[str, object]:
        jobs, _ = self._cron_modules()
        with self._cron_scope(jobs):
            job = jobs.get_job(job_id)
            if job is None:
                return {"removed": False}
            with self._authority_scope() as authority:
                revoked = authority.revoke_if_owner(
                    str(job["id"]), owner=owner, session=session
                )
            if revoked is None and job.get("enabled"):
                raise AutomationDenied("grant_unavailable")
            removed = bool(jobs.remove_job(str(job["id"])))
        return {"removed": removed, "inflight": int(revoked or 0)}

    def request_activation(
        self, envelope: IPCRequestEnvelope, job_id: str
    ) -> dict[str, object]:
        jobs, _ = self._cron_modules()
        with self._cron_scope(jobs):
            job = jobs.get_job(job_id)
        if job is None:
            raise AutomationDenied("job_not_found")
        until = min(self.clock() + 86400, self.clock() + 24 * 60 * 60)
        with self._authority_scope() as authority:
            binding = authority.binding(
                job, envelope.caller_id, envelope.session_id, until
            )
        pending = self.consent.register(
            request_id=envelope.request_id,
            caller_id=envelope.caller_id,
            session_id=envelope.session_id,
            job=job,
            binding_fingerprint=binding,
            until=until,
        )
        return {
            "state": "awaiting_approval",
            "consent": pending.presentation(now=self.consent.now()),
        }

    def decide_activation(
        self,
        envelope: IPCRequestEnvelope,
        *,
        verifier: ConsentSignatureVerifier,
    ) -> IPCResponseEnvelope:
        if not verifier.available:
            return _failure(envelope, "consent_unavailable")
        try:
            consent_id, decision, signature = _decode_decision(envelope)
            pending = self.consent.get(
                consent_id,
                request_id=envelope.request_id,
                caller_id=envelope.caller_id,
                session_id=envelope.session_id,
            )
        except ConsentError as error:
            return _failure(envelope, error.code)
        except ValueError:
            return _failure(envelope, "malformed_payload")
        if not verifier.verify(_signature_message(pending, decision), signature):
            return _failure(envelope, "invalid_consent_signature")
        try:
            decided = self.consent.decide(consent_id, decision)
        except ConsentError as error:
            return _failure(envelope, error.code)
        if decision is ConsentDecision.REJECT:
            return IPCResponseEnvelope.success(envelope.request_id, {"state": "denied"})
        try:
            job = self._activate(decided)
        except (
            AutomationDenied,
            ApprovalError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            return _failure(
                envelope, getattr(error, "code", "automation_activation_failed")
            )
        return IPCResponseEnvelope.success(
            envelope.request_id, {"state": "activated", "job": self._job_summary(job)}
        )

    def global_stop(self) -> dict[str, object]:
        jobs, _ = self._cron_modules()
        with self._authority_scope() as authority:
            active = authority.global_stop()
        paused = 0
        with self._cron_scope(jobs):
            for job in jobs.list_jobs(include_disabled=True):
                if job.get("enabled"):
                    jobs.pause_job(str(job["id"]), reason="global_stop")
                    paused += 1
        return {"stopped": True, "inflight": int(active), "paused": paused}

    def close(self) -> None:
        return None

    def _activate(self, consent: PendingAutomationConsent) -> dict[str, Any]:
        jobs, _ = self._cron_modules()
        with self._cron_scope(jobs):
            job = jobs.get_job(consent.job_id)
            if job is None:
                raise AutomationDenied("job_not_found")
        approval = self.approvals.issue(
            binding_fingerprint=consent.binding_fingerprint,
            caller_id=consent.caller_id,
            session_id=consent.session_id,
            ttl_seconds=30.0,
        )
        available = self.approvals.make_available(approval.approval_id)
        with self._authority_scope() as authority:
            authority.activate(
                job,
                owner=consent.caller_id,
                session=consent.session_id,
                until=consent.until,
                approval_id=available.approval_id,
            )
            try:
                with self._cron_scope(jobs):
                    resumed = jobs.resume_job(consent.job_id)
            except BaseException:
                authority.revoke_if_owner(
                    consent.job_id, owner=consent.caller_id, session=consent.session_id
                )
                raise
        if resumed is None:
            raise AutomationDenied("job_not_found")
        return resumed

    @contextmanager
    def _authority_scope(self) -> Any:
        with self._lock:
            self._prepare_home()
            authority = AutomationAuthority(
                self.home / "jl-authorization", self.approvals, clock=self.clock
            )
            try:
                yield authority
            finally:
                authority.close()

    def _prepare_home(self) -> None:
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.home, 0o700)
        config = self.home / "config.yaml"
        if not config.exists():
            config.write_text("cron:\n  execution_policy: jl\n", encoding="utf-8")
            os.chmod(config, 0o600)
        details = config.lstat()
        if (
            details.st_uid != os.geteuid()
            or not stat.S_ISREG(details.st_mode)
            or stat.S_IMODE(details.st_mode) != 0o600
        ):
            raise AutomationDenied("unsafe_config")

    def _cron_modules(self) -> tuple[Any, Any]:
        with self._lock:
            self._prepare_home()
            if not self._imports_ready:
                sys.path.insert(0, str(self.upstream))
                self._imports_ready = True
            jobs = importlib.import_module("cron.jobs")
            executions = importlib.import_module("cron.executions")
            return jobs, executions

    @contextmanager
    def _cron_scope(self, jobs: Any) -> Any:
        constants = importlib.import_module("hermes_constants")

        token = constants.set_hermes_home_override(str(self.home))
        try:
            with jobs.use_cron_store(self.home):
                yield
        finally:
            constants.reset_hermes_home_override(token)

    @staticmethod
    def _job_summary(job: dict[str, Any]) -> dict[str, object]:
        latest = job.get("latest_execution")
        return {
            "id": str(job.get("id") or ""),
            "name": str(job.get("name") or ""),
            "prompt": str(job.get("prompt") or ""),
            "schedule_display": str(job.get("schedule_display") or ""),
            "enabled": bool(job.get("enabled")),
            "state": str(job.get("state") or ""),
            "next_run_at": job.get("next_run_at"),
            "last_run_at": job.get("last_run_at"),
            "last_status": job.get("last_status"),
            "paused_reason": job.get("paused_reason"),
            "latest_execution": (
                AutomationManager._execution_summary(latest)
                if isinstance(latest, dict)
                else None
            ),
        }

    @staticmethod
    def _execution_summary(item: dict[str, Any]) -> dict[str, object]:
        return {
            "id": str(item.get("id") or ""),
            "job_id": str(item.get("job_id") or ""),
            "status": str(item.get("status") or ""),
            "claimed_at": item.get("claimed_at"),
            "started_at": item.get("started_at"),
            "finished_at": item.get("finished_at"),
            "scheduled_instant": item.get("scheduled_instant"),
            "error": item.get("error"),
        }


class AutomationRequestHandler:
    def __init__(self, manager: AutomationManager, scheduler_enabled: Any) -> None:
        self.manager = manager
        self.scheduler_enabled = scheduler_enabled

    def __call__(self, envelope: IPCRequestEnvelope) -> IPCResponseEnvelope:
        try:
            if envelope.operation == "automation-status":
                _empty(envelope.payload)
                return self._ok(
                    envelope,
                    self.manager.status(scheduler_enabled=bool(self.scheduler_enabled())),
                )
            if envelope.operation == "automation-list":
                _empty(envelope.payload)
                return self._ok(envelope, self.manager.list_jobs())
            if envelope.operation == "automation-history":
                return self._ok(
                    envelope,
                    self.manager.history(
                        _optional_text(envelope.payload.get("job_id"), "job_id", 128),
                        _limit(envelope.payload.get("limit", 50)),
                    ),
                )
            if envelope.operation == "automation-create":
                return self._ok(envelope, self.manager.create(dict(envelope.payload)))
            if envelope.operation == "automation-resume":
                job_id = _bounded(envelope.payload.get("job_id"), "job_id", 128)
                return self._ok(
                    envelope, self.manager.request_activation(envelope, job_id)
                )
            if envelope.operation == "automation-pause":
                job_id = _bounded(envelope.payload.get("job_id"), "job_id", 128)
                return self._ok(
                    envelope,
                    self.manager.pause(
                        job_id, owner=envelope.caller_id, session=envelope.session_id
                    ),
                )
            if envelope.operation == "automation-remove":
                job_id = _bounded(envelope.payload.get("job_id"), "job_id", 128)
                return self._ok(
                    envelope,
                    self.manager.remove(
                        job_id, owner=envelope.caller_id, session=envelope.session_id
                    ),
                )
            if envelope.operation == "automation-stop-all":
                _empty(envelope.payload)
                return self._ok(envelope, self.manager.global_stop())
        except (AutomationDenied, ValueError, OSError, RuntimeError):
            return _failure(envelope, "automation_denied")
        return _failure(envelope, "unsupported_operation")

    @staticmethod
    def _ok(
        envelope: IPCRequestEnvelope, result: dict[str, object]
    ) -> IPCResponseEnvelope:
        return IPCResponseEnvelope.success(envelope.request_id, result)


class AutomationConsentRequestHandler:
    def __init__(
        self, manager: AutomationManager, verifier: ConsentSignatureVerifier
    ) -> None:
        self.manager = manager
        self.verifier = verifier

    def __call__(self, envelope: IPCRequestEnvelope) -> IPCResponseEnvelope | None:
        if envelope.operation != "automation-consent-decision":
            return None
        return self.manager.decide_activation(envelope, verifier=self.verifier)


def _signature_message(
    record: PendingAutomationConsent, decision: ConsentDecision
) -> bytes:
    message = bytearray(b"jl-agent-consent-v1\0")
    for field in (
        record.consent_id,
        record.request_id,
        record.caller_id,
        record.session_id,
        decision.value,
        record.nonce,
    ):
        encoded = field.encode("utf-8")
        message.extend(len(encoded).to_bytes(8, "big"))
        message.extend(encoded)
    return bytes(message)


def _decode_decision(envelope: IPCRequestEnvelope) -> tuple[str, ConsentDecision, str]:
    payload = dict(envelope.payload)
    if set(payload) != {"consent_id", "decision", "signature"}:
        raise ValueError("automation consent payload fields do not match")
    consent_id = _bounded(payload["consent_id"], "consent_id", 128)
    signature = _bounded(payload["signature"], "signature", 4096)
    try:
        decision = ConsentDecision(payload["decision"])
    except (TypeError, ValueError) as error:
        raise ValueError("invalid automation consent decision") from error
    return consent_id, decision, signature


def _failure(envelope: IPCRequestEnvelope, code: str) -> IPCResponseEnvelope:
    return IPCResponseEnvelope.failure(
        envelope.request_id, code, "automation request was denied"
    )


def _empty(payload: Any) -> None:
    if payload:
        raise ValueError("payload must be empty")


def _bounded(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{field} must be bounded text")
    return value


def _optional_text(value: Any, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    if value == "":
        return None
    return _bounded(value, field, maximum)


def _limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("invalid limit")
    return value


def _summary(value: str, maximum: int) -> str:
    return " ".join(value.split())[:maximum]
