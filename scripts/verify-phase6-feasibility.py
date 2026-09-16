#!/usr/bin/env python3
"""Offline Hermes store probe; deliberately never dispatches a job.

This is a feasibility diagnostic, not an automation implementation or sandbox.
Run in a fresh interpreter with the project's Python. All test state is temporary
and project-local. No provider, script, scheduler, or delivery is invoked.
"""

import ast
import importlib
import inspect
import json
import logging
import os
import sys
import tempfile
from pathlib import Path


class DiagnosticLog(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def main():
    root = Path(__file__).resolve().parents[1]
    upstream = root / "upstream/hermes-agent"
    sys.dont_write_bytecode = True
    scratch = root / "artifacts"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="phase6-probe-", dir=scratch) as home:
        os.environ.clear()
        os.environ.update(HERMES_HOME=home, TZ="UTC", PYTHONDONTWRITEBYTECODE="1")
        sys.path.insert(0, str(upstream))
        jobs = importlib.import_module("cron.jobs")
        provider = importlib.import_module("cron.scheduler_provider")
        diagnostics = DiagnosticLog()
        logging.getLogger().addHandler(diagnostics)
        assert jobs.HERMES_DIR == Path(home).resolve()
        job = jobs.create_job(
            prompt="Offline feasibility fixture; never execute",
            schedule="every 1h",
            no_agent=True,
            script="jl-reminder-fixture.py",
            deliver="local",
            failure_deliver="local",
            paused=True,
        )
        job_id = job["id"]
        assert job["state"] == "paused"
        assert jobs.claim_job_for_fire(job_id, return_job=True) is False
        jobs.resume_job(job_id)
        claimed = jobs.claim_job_for_fire(job_id, return_job=True)
        assert isinstance(claimed, dict)
        assert jobs.claim_job_for_fire(job_id, return_job=True) is False
        jobs.pause_job(job_id, reason="Probe revocation")
        assert jobs.claim_job_for_fire(job_id, return_job=True) is False
        assert jobs.get_job(job_id)["state"] == "paused"
        # A previously claimed snapshot is not retroactively revoked by pause.
        assert claimed["state"] == "scheduled"
        jobs = importlib.reload(jobs)
        assert jobs.get_job(job_id)["state"] == "paused"
        assert jobs.claim_job_for_fire(job_id, return_job=True) is False

        tree = ast.parse((upstream / "cron/scheduler.py").read_text())
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        process = functions["_process_due_job"]
        calls = {
            n.func.id for n in ast.walk(process)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert {"claim_job_for_fire", "run_one_job"} <= calls
        print(json.dumps({
            "store_checks": {
                "project_profile": "PASS",
                "created_paused": "PASS",
                "duplicate_live_claim_denied": "PASS",
                "pause_blocks_new_claim": "PASS",
                "reload_preserves_pause": "PASS",
                "previous_claim_snapshot_still_scheduled": "CONFIRMED",
            },
            "builtin_start_signature": str(inspect.signature(
                provider.InProcessCronScheduler.start)),
            "builtin_dispatch_calls": sorted(calls),
            "execution_performed": False,
            "occurrence_ledger": (
                "DEGRADED: completed-occurrence lookup failed"
                if any(r.name == "cron.occurrences" for r in diagnostics.records)
                else "No lookup error observed; crash recovery not tested"
            ),
            "phase6_feasibility": "BLOCKED: missing per-job JL gate",
            "limits": "Store reload is not worker restart; no effects executed.",
        }, indent=2))


if __name__ == "__main__":
    main()
