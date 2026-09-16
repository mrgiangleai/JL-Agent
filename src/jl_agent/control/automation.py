"""Durable exact grants above Hermes; no scheduling or tool execution here."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .approvals import ApprovalError, OneTimeApprovalStore

# Audited program has no imports, inputs, file access or network capability use.
REMINDER_SCRIPT = b'print("JL_REMINDER_DONE")\n'
SCRIPT_NAME = "jl-reminder.py"
_RUNTIME_FIELDS = frozenset(
    """enabled state paused_at paused_reason next_run_at
    last_run_at last_status last_error last_delivery_error last_delivery_unverified
    failure_streak fire_claim run_claim execution_id _scheduled_instant
    preflight_alerted last_fire_error last_delivery_queued""".split()
)
_FIELDS = frozenset(
    """id name prompt skills skill model provider base_url
    provider_snapshot model_snapshot script no_agent monitor_script monitor_url
    monitor_state context_from schedule schedule_display repeat created_at deliver
    origin enabled_toolsets workdir attach_to_session reasoning_effort
    failure_deliver""".split()
)


class AutomationDenied(RuntimeError):
    """Admission failed; no authority is returned."""


def _encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_encode(value).encode()).hexdigest()


def semantic_fingerprint(job: dict[str, Any]) -> str:
    if set(job) - (_FIELDS | _RUNTIME_FIELDS):
        raise AutomationDenied("unknown_job_field")
    if (
        job.get("no_agent") is not True
        or job.get("script") != SCRIPT_NAME
        or job.get("deliver") != "local"
        or job.get("failure_deliver") != "local"
    ):
        raise AutomationDenied("forbidden_job")
    forbidden = _FIELDS - {
        "id",
        "name",
        "prompt",
        "script",
        "no_agent",
        "schedule",
        "schedule_display",
        "repeat",
        "created_at",
        "deliver",
        "failure_deliver",
    }
    if any(job.get(k) not in (None, [], "", False) for k in forbidden):
        raise AutomationDenied("forbidden_job_field")
    for key in ("id", "name", "prompt"):
        if not isinstance(job.get(key), str) or not 0 < len(job[key]) <= 4096:
            raise AutomationDenied("invalid_job_text")
    schedule = job.get("schedule")
    repeat = job.get("repeat")
    if (
        not isinstance(schedule, dict)
        or schedule.get("kind") not in {"once", "interval"}
        or not isinstance(repeat, dict)
        or set(repeat) != {"times", "completed"}
    ):
        raise AutomationDenied("invalid_schedule")
    semantic = {k: v for k, v in job.items() if k not in _RUNTIME_FIELDS}
    semantic["repeat"] = {"times": repeat["times"]}
    return _digest(semantic)


def _private(path: Path, directory: bool = False) -> None:
    info = path.lstat()
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (
        not valid
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
    ):
        raise AutomationDenied("unsafe_authority_storage")


class AutomationAuthority:
    """One process owns a grant ledger. SQLite transactions serialize admission.

    Occurrence receipts are authorization replay tombstones, not a job store or
    retry queue. Never prune them to regain permission. Capacity exhaustion denies.
    """

    def __init__(
        self,
        home: Path,
        approvals: OneTimeApprovalStore,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if home.is_symlink():
            raise AutomationDenied("unsafe_authority_storage")
        self.home = home.resolve()
        self.approvals = approvals
        self.clock = clock
        self._lock = threading.RLock()
        self._active = 0
        self._faulted = False
        self._closed = False
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        _private(self.home, True)
        lock_path = self.home / "authorization.lock"
        existed = lock_path.exists()
        self._fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            _private(lock_path)
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.path = self.home / "authorization.sqlite"
            if existed and not self.path.exists():
                raise AutomationDenied("authority_ledger_missing")
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            _private(self.path)
            self._db = sqlite3.connect(self.path, timeout=2, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY, stopped INTEGER);
                INSERT OR IGNORE INTO settings VALUES (1, 0);
                CREATE TABLE IF NOT EXISTS grants (
                    job TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    session TEXT NOT NULL, until REAL NOT NULL,
                    revoked INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (
                    execution TEXT PRIMARY KEY, job TEXT NOT NULL,
                    occurrence TEXT NOT NULL,
                    state TEXT NOT NULL, UNIQUE(job, occurrence));
            """)
            if self._db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise AutomationDenied("authority_corrupt")
            with self._db:
                self._db.execute(
                    "UPDATE receipts SET state='unknown' WHERE state='inflight'"
                )
        except BaseException:
            if hasattr(self, "_db"):
                self._db.close()
            os.close(self._fd)
            raise

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self._faulted or self._closed:
                raise AutomationDenied("authority_unavailable")
            try:
                _private(self.home, True)
                _private(self.path)
                self._db.execute("BEGIN IMMEDIATE")
                yield self._db
                self._db.commit()
            except (AutomationDenied, ApprovalError):
                self._db.rollback()
                raise
            except BaseException:
                self._faulted = True
                self._db.rollback()
                raise

    def _grant_fingerprint(self, job: dict[str, Any]) -> str:
        config = (self.home.parent / "config.yaml").read_bytes()
        return _digest(
            {
                "job": semantic_fingerprint(job),
                "config": hashlib.sha256(config).hexdigest(),
                "script": hashlib.sha256(REMINDER_SCRIPT).hexdigest(),
            }
        )

    def binding(
        self, job: dict[str, Any], owner: str, session: str, until: float
    ) -> str:
        if (
            not owner
            or not session
            or len(owner) > 256
            or len(session) > 256
            or not math.isfinite(until)
            or not self.clock() < until <= self.clock() + 86400
        ):
            raise AutomationDenied("invalid_grant")
        return _digest(
            {
                "purpose": "jl-automation-grant-v1",
                "home": str(self.home),
                "job": self._grant_fingerprint(job),
                "owner": owner,
                "session": session,
                "until": until,
                "script": hashlib.sha256(REMINDER_SCRIPT).hexdigest(),
            }
        )

    def activate(
        self,
        job: dict[str, Any],
        *,
        owner: str,
        session: str,
        until: float,
        approval_id: str,
    ) -> None:
        """Consume trusted exact consent; never issue it or resume a Hermes job."""
        job = json.loads(_encode(job))
        binding = self.binding(job, owner, session, until)
        if job.get("state") != "paused" or job.get("enabled") is not False:
            raise AutomationDenied("activation_requires_paused_job")
        with self._transaction() as db:
            if db.execute("SELECT stopped FROM settings WHERE id=1").fetchone()[0]:
                raise AutomationDenied("global_stop")
            previous = db.execute(
                "SELECT owner,session FROM grants WHERE job=?", (job["id"],)
            ).fetchone()
            if previous and (previous["owner"], previous["session"]) != (
                owner,
                session,
            ):
                raise AutomationDenied("grant_owner_mismatch")
            if (
                previous is None
                and db.execute("SELECT count(*) FROM grants").fetchone()[0] >= 1000
            ):
                raise AutomationDenied("grant_capacity")
            self.approvals.consume(
                approval_id,
                binding_fingerprint=binding,
                caller_id=owner,
                session_id=session,
            )
            db.execute(
                "INSERT OR REPLACE INTO grants VALUES (?,?,?,?,?,0)",
                (job["id"], self._grant_fingerprint(job), owner, session, until),
            )

    def revoke(self, job_id: str, *, owner: str, session: str) -> int:
        with self._transaction() as db:
            row = db.execute(
                "SELECT owner,session FROM grants WHERE job=?", (job_id,)
            ).fetchone()
            if row is None or (row["owner"], row["session"]) != (owner, session):
                raise AutomationDenied("grant_owner_mismatch")
            db.execute("UPDATE grants SET revoked=1 WHERE job=?", (job_id,))
            return db.execute(
                "SELECT count(*) FROM receipts WHERE job=? AND state='inflight'",
                (job_id,),
            ).fetchone()[0]

    def revoke_if_owner(self, job_id: str, *, owner: str, session: str) -> int | None:
        with self._transaction() as db:
            row = db.execute(
                "SELECT owner,session FROM grants WHERE job=?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            if (row["owner"], row["session"]) != (owner, session):
                raise AutomationDenied("grant_owner_mismatch")
            db.execute("UPDATE grants SET revoked=1 WHERE job=?", (job_id,))
            return db.execute(
                "SELECT count(*) FROM receipts WHERE job=? AND state='inflight'",
                (job_id,),
            ).fetchone()[0]

    def stopped(self) -> bool:
        with self._transaction() as db:
            return bool(
                db.execute("SELECT stopped FROM settings WHERE id=1").fetchone()[0]
            )

    def global_stop(self) -> int:
        with self._transaction() as db:
            db.execute("UPDATE settings SET stopped=1 WHERE id=1")
            return self._active

    def dispatch_allowed(self) -> bool:
        """Read admission state for the host's Hermes drain gate."""
        with self._transaction() as db:
            return not bool(
                db.execute("SELECT stopped FROM settings WHERE id=1").fetchone()[0]
            )

    @contextmanager
    def admit(self, job: dict[str, Any], execution: str) -> Iterator[float]:
        fingerprint = self._grant_fingerprint(job)
        instant = job.get("_scheduled_instant")
        if not isinstance(instant, str) or not instant or not execution:
            raise AutomationDenied("missing_occurrence")
        with self._lock:
            with self._transaction() as db:
                row = db.execute(
                    "SELECT * FROM grants WHERE job=?", (job["id"],)
                ).fetchone()
                if db.execute("SELECT stopped FROM settings WHERE id=1").fetchone()[0]:
                    raise AutomationDenied("global_stop")
                if (
                    row is None
                    or row["revoked"]
                    or self.clock() >= row["until"]
                    or row["fingerprint"] != fingerprint
                ):
                    raise AutomationDenied("grant_unavailable")
                if db.execute("SELECT count(*) FROM receipts").fetchone()[0] >= 10000:
                    raise AutomationDenied("receipt_capacity")
                if db.execute(
                    "SELECT 1 FROM receipts WHERE execution=? "
                    "OR (job=? AND occurrence=?)",
                    (execution, job["id"], instant),
                ).fetchone():
                    raise AutomationDenied("occurrence_replayed")
                db.execute(
                    "INSERT INTO receipts VALUES (?,?,?,'inflight')",
                    (execution, job["id"], instant),
                )
                remaining = row["until"] - self.clock()
            self._active += 1
        try:
            yield min(10.0, remaining)
        finally:
            try:
                with self._transaction() as db:
                    # The hook context does not report effect success. Spent means
                    # never authorize this occurrence again, not delivered.
                    db.execute(
                        "UPDATE receipts SET state='spent' WHERE execution=?",
                        (execution,),
                    )
            finally:
                with self._lock:
                    self._active -= 1

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._active:
                raise AutomationDenied("authority_inflight")
            self._closed = True
            self._db.close()
            os.close(self._fd)
