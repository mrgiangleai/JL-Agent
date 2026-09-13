"""Bounded, metadata-only JSONL audit ledger for JL runtime events."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AuditEvent:
    timestamp: str
    event: str
    request_id: str
    caller_id: str
    session_id: str
    capability_id: str = ""
    capability_version: str = ""
    action_class: tuple[str, ...] = ()
    policy_decision: str = ""
    fingerprint_reference: str = ""
    approval_id_reference: str = ""
    approval_consumed: bool = False
    provider: str = ""
    model: str = ""
    error_category: str = ""
    duration_ms: int | None = None


class AuditLedger:
    """Append allowlisted metadata and retain only the newest bounded events."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_events: int = 1000,
        max_bytes: int = 2 * 1024 * 1024,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if max_events < 1 or max_bytes < 1024:
            raise ValueError("audit retention bounds are invalid")
        self.path = Path(path)
        self.max_events = max_events
        self.max_bytes = max_bytes
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._prepare_storage()

    def record(self, event: str, **metadata: object) -> AuditEvent:
        allowed = {
            field.name
            for field in AuditEvent.__dataclass_fields__.values()
            if field.name not in {"timestamp", "event"}
        }
        unknown = set(metadata).difference(allowed)
        if unknown:
            raise ValueError(f"audit metadata is not allowlisted: {sorted(unknown)}")
        item = AuditEvent(
            timestamp=self._now().isoformat(),
            event=event,
            **metadata,  # type: ignore[arg-type]
        )
        encoded = (
            json.dumps(
                asdict(item),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
        if len(encoded) > self.max_bytes:
            raise ValueError("single audit event exceeds retention bound")
        with self._lock:
            with self.path.open("ab") as stream:
                stream.write(encoded)
            os.chmod(self.path, 0o600)
            self._trim_locked()
        return item

    def read(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            lines = self.path.read_bytes().splitlines()
        return tuple(json.loads(line) for line in lines if line)

    def safe_activity(self, limit: int = 50) -> tuple[dict[str, object], ...]:
        """Return a bounded UI projection with no identity or secret references."""
        if not 1 <= limit <= 100:
            raise ValueError("activity limit must be between 1 and 100")
        events = self.read()[-limit:]
        projected: list[dict[str, object]] = []
        for item in reversed(events):
            projected.append(
                {
                "timestamp": _text(item.get("timestamp")),
                "capability_id": _text(item.get("capability_id")),
                "action_class": _strings(item.get("action_class")),
                "policy_decision": _text(item.get("policy_decision")),
                "execution_status": _execution_status(_text(item.get("event"))),
                }
            )
        return tuple(projected)

    def _prepare_storage(self) -> None:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        parent = self.path.parent.stat()
        if parent.st_uid != os.geteuid() or not stat.S_ISDIR(parent.st_mode):
            raise PermissionError("audit directory must be owned by the current user")
        if stat.S_IMODE(parent.st_mode) & 0o077:
            raise PermissionError("audit directory permissions must be user-only")
        if not self.path.exists():
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        details = self.path.stat()
        if (
            details.st_uid != os.geteuid()
            or not stat.S_ISREG(details.st_mode)
            or stat.S_IMODE(details.st_mode) != 0o600
        ):
            raise PermissionError("audit ledger must be a private user-owned file")

    def _trim_locked(self) -> None:
        content = self.path.read_bytes()
        lines = content.splitlines(keepends=True)
        while len(lines) > self.max_events or sum(map(len, lines)) > self.max_bytes:
            lines.pop(0)
        retained = b"".join(lines)
        if retained == content:
            return
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(retained)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.path)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass
            raise


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)][:8]


def _execution_status(event: str) -> str:
    return {
        "execution_prepared": "prepared",
        "execution_started": "executing",
        "execution_completed": "completed",
        "execution_denied": "denied",
        "execution_failed": "failed",
        "request_denied": "denied",
    }.get(event, "unknown")
