"""Inert runtime composition and production policy for the Hermes cron hook."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .control.approvals import OneTimeApprovalStore
from .control.automation import REMINDER_SCRIPT, AutomationAuthority, AutomationDenied
from .control.hermes_projection import HermesProjection

_HOOK_FILES = {
    "execution_policy.py": (
        "00aee1e4471060ba0602bc8cfeabb22d01e59c1b840b68a537b2730316c7e8aa"
    ),
    "scheduler.py": "4623c40ba022f26cc56a54e8a050c0487979c646ddb2bc2db2cab72f6d36bd94",
    "scheduler_script.py": (
        "84b6a68287614d793b8e78d949f0db19f74e9bd2a55ddfb3bec995c03722ec06"
    ),
}


class AutomationRuntime:
    """No timer, start method, IPC operation or implicit Hermes import.

    bind_worker is for an explicitly prepared isolated process whose Hermes home
    is already set. Main runtime construction does not call it.
    """

    scheduler_enabled = False

    def __init__(
        self, upstream: Path, home: Path, approvals: OneTimeApprovalStore
    ) -> None:
        self.upstream = upstream.resolve()
        self.home = home.resolve()
        self.approvals = approvals
        self.authority: AutomationAuthority | None = None
        self._hook: Any = None
        self._closed = False

    def bind_worker(self) -> AutomationAuthority:
        if self._closed or self.authority is not None:
            raise AutomationDenied("worker_already_bound_or_closed")
        if Path(os.environ.get("HERMES_HOME", "")).resolve() != self.home:
            raise AutomationDenied("worker_profile_mismatch")
        HermesProjection(self.upstream).inspect_identity()
        for name, expected in _HOOK_FILES.items():
            digest = hashlib.sha256(
                (self.upstream / "cron" / name).read_bytes()
            ).hexdigest()
            if digest != expected:
                raise AutomationDenied("worker_hook_changed")
        sys.path.insert(0, str(self.upstream))
        hook = importlib.import_module("cron.execution_policy")
        if (
            Path(hook.__file__ or "").resolve()
            != self.upstream / "cron/execution_policy.py"
        ):
            raise AutomationDenied("worker_hook_mismatch")
        authority = AutomationAuthority(self.home / "jl-authorization", self.approvals)
        try:
            hook.register_execution_policy(self.home, "jl", self)
        except BaseException:
            authority.close()
            raise
        self.authority, self._hook = authority, hook
        return authority

    @contextmanager
    def acquire(self, request: Any) -> Iterator[Any]:
        authority = self.authority
        if self._closed or authority is None or request.profile_home != self.home:
            raise AutomationDenied("worker_unavailable")
        if len(request.job_json) > 65536:
            raise AutomationDenied("request_too_large")
        expected = hashlib.sha256(
            (self.home.as_posix() + request.job_json).encode()
        ).hexdigest()
        job = json.loads(request.job_json)
        if (
            expected != request.fingerprint
            or job.get("execution_id") != request.execution_id
            or job.get("state") != "scheduled"
            or job.get("enabled") is not True
        ):
            raise AutomationDenied("request_mismatch")
        with authority.admit(job, request.execution_id) as remaining:
            yield self._hook.ExecutionLease(
                request.fingerprint, REMINDER_SCRIPT, time.monotonic() + remaining
            )

    def global_stop(self) -> int:
        if self._closed:
            return 0
        if self.authority is None:
            self._closed = True
            return 0
        return self.authority.global_stop()

    def shutdown(self) -> None:
        if self._closed:
            return
        inflight = self.global_stop()
        if inflight:
            raise AutomationDenied("automation_draining")
        if self.authority is not None:
            self.authority.close()
        self._closed = True
