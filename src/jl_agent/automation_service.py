"""Explicit foreground scheduler worker; Hermes owns every scheduling tick."""

from __future__ import annotations

import argparse
import importlib
import os
import plistlib
import signal
import subprocess
import threading
import time
from pathlib import Path

from .automation_runtime import AutomationRuntime
from .control.approvals import OneTimeApprovalStore
from .control.automation import AutomationDenied


def require_internal_apfs(home: Path) -> None:
    """Verify the actual backing device, including paths below a mount point."""
    target = home.resolve()
    while not target.exists():
        target = target.parent
    result = subprocess.run(
        ["/bin/df", "-P", str(target)],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    device = result.stdout.splitlines()[-1].split()[0]
    if not device.startswith("/dev/"):
        raise AutomationDenied("runtime_device_unavailable")
    result = subprocess.run(
        ["/usr/sbin/diskutil", "info", "-plist", device],
        check=True,
        capture_output=True,
        timeout=5,
    )
    info = plistlib.loads(result.stdout)
    if info.get("FilesystemType") != "apfs" or info.get("Internal") is not True:
        raise AutomationDenied("runtime_requires_internal_apfs")


class HermesSchedulerService:
    """A lifecycle adapter, not a scheduler. No grant creation or auto-restart."""

    def __init__(self, runtime: AutomationRuntime) -> None:
        self.runtime = runtime
        self._started = False
        self.failure: BaseException | None = None

    def serve(self, stop: threading.Event, *, interval: int = 60) -> None:
        if self._started or not 1 <= interval <= 60:
            raise AutomationDenied("scheduler_start_invalid")
        require_internal_apfs(self.runtime.home)
        authority = self.runtime.authority
        if authority is None:
            authority = self.runtime.bind_worker()
        if not authority.dispatch_allowed():
            raise AutomationDenied("scheduler_globally_stopped")
        self._started = True
        scheduler = importlib.import_module("cron.scheduler")
        provider = importlib.import_module("cron.scheduler_provider")
        jobs = importlib.import_module("cron.jobs")
        hook = importlib.import_module("cron.execution_policy")

        def can_dispatch() -> bool:
            if stop.is_set():
                return False
            try:
                if (
                    hook.resolve_policy() is None
                    or not authority.dispatch_allowed()
                    or jobs.get_ticker_last_error()
                ):
                    raise AutomationDenied("scheduler_dispatch_closed")
                return True
            except BaseException as error:
                self.failure = error
                stop.set()
                return False

        self.runtime.scheduler_enabled = True
        try:
            provider.InProcessCronScheduler().start(
                stop, interval=interval, can_dispatch=can_dispatch
            )
        finally:
            stop.set()
            self.runtime.scheduler_enabled = False
            try:
                authority.global_stop()
            except BaseException as error:
                self.failure = error
            deadline = time.monotonic() + 20
            pause = threading.Event()
            while scheduler.get_running_job_ids():
                if time.monotonic() >= deadline:
                    raise AutomationDenied("scheduler_drain_timeout_no_retry")
                pause.wait(0.05)
            try:
                self.runtime.shutdown()
            except BaseException as error:
                self.failure = error
                # Admission is fault-latched. Release resources after draining even
                # when the durable stop cannot be written; never resume this worker.
                authority.close()
        if self.failure is not None:
            raise AutomationDenied(
                "scheduler_stopped_on_error_no_retry"
            ) from self.failure


def main() -> None:
    parser = argparse.ArgumentParser(description="Explicit JL Hermes scheduler worker")
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    home = args.home.resolve()
    # This process is exclusively the cron worker. Never inherit provider secrets.
    os.environ.clear()
    os.environ.update(
        HERMES_HOME=str(home),
        PATH="/usr/bin:/bin:/usr/sbin:/sbin",
        TZ="UTC",
        PYTHONDONTWRITEBYTECODE="1",
    )
    runtime = AutomationRuntime(
        Path(__file__).resolve().parents[2] / "upstream/hermes-agent",
        home,
        OneTimeApprovalStore(),
    )
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    HermesSchedulerService(runtime).serve(stop)


if __name__ == "__main__":
    main()
