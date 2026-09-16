#!/usr/bin/env python3
"""Single explicitly authorized local smoke. Existing home ALWAYS refuses rerun.

This operator harness is not an IPC or UI approval issuer. Its exact fixed job
is authorized by the user's Phase 6 live-smoke instruction. Never retry it.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sqlite3
import sys
import threading
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jl_agent.automation_runtime import AutomationRuntime
from jl_agent.automation_service import HermesSchedulerService, require_internal_apfs
from jl_agent.control.approvals import ApprovalState, OneTimeApprovalStore
from jl_agent.control.automation import REMINDER_SCRIPT, SCRIPT_NAME


def save(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(payload, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    home = args.home.resolve()
    require_internal_apfs(home)
    home.mkdir(mode=0o700, parents=True, exist_ok=False)
    save(home / "attempt.json", {"status": "attempt_started", "no_retry": True})
    os.environ.clear()
    os.environ.update(
        HERMES_HOME=str(home),
        PATH="/usr/bin:/bin:/usr/sbin:/sbin",
        TZ="UTC",
        PYTHONDONTWRITEBYTECODE="1",
    )
    config = home / "config.yaml"
    config.write_text(
        "cron:\n  execution_policy: jl\n  max_parallel_jobs: 1\n"
        "database:\n  journal_mode: delete\n",
        encoding="utf-8",
    )
    config.chmod(0o600)
    network_attempts = []
    script_launches = []
    forbidden_imports = []

    def audit(event, arguments):
        if event == "import" and arguments[0] in {
            "hermes_cli.env_loader",
            "run_agent",
            "tools.mcp_tool",
        }:
            forbidden_imports.append(arguments[0])
            raise PermissionError("live smoke autonomous imports denied")
        if event == "subprocess.Popen" and arguments[1] == [sys.executable, "-I", "-"]:
            script_launches.append("fixed_python_stdin")
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            network_attempts.append(event)
            raise PermissionError("live smoke network denied")

    sys.addaudithook(audit)
    approvals = OneTimeApprovalStore()
    runtime = AutomationRuntime(
        Path(__file__).resolve().parents[1] / "upstream/hermes-agent", home, approvals
    )
    authority = runtime.bind_worker()
    jobs = importlib.import_module("cron.jobs")
    executions = importlib.import_module("cron.executions")
    occurrence = importlib.import_module("cron.occurrences")
    run_at = (datetime.now(UTC) + timedelta(seconds=3)).isoformat()
    job = jobs.create_job(
        "Phase 6 authorized local smoke",
        run_at,
        name="Phase 6 local smoke",
        no_agent=True,
        script=SCRIPT_NAME,
        deliver="local",
        failure_deliver="local",
        paused=True,
    )
    until = time.time() + 180
    binding = authority.binding(job, "local-operator", "phase6-live-smoke", until)
    approval = approvals.issue(
        binding_fingerprint=binding,
        caller_id="local-operator",
        session_id="phase6-live-smoke",
        ttl_seconds=60,
    )
    approvals.make_available(approval.approval_id)
    authority.activate(
        job,
        owner="local-operator",
        session="phase6-live-smoke",
        until=until,
        approval_id=approval.approval_id,
    )
    consumed = approvals.get(approval.approval_id).state is ApprovalState.CONSUMED
    save(
        home / "authorization-evidence.json",
        {
            "job_id": job["id"],
            "created_paused": job["state"] == "paused",
            "run_at": run_at,
            "approval_consumed": consumed,
            "approval_binding": binding,
            "script_sha256": hashlib.sha256(REMINDER_SCRIPT).hexdigest(),
            "grant_valid_until": until,
            "destination": "local",
        },
    )
    jobs.resume_job(job["id"])
    stop = threading.Event()
    observation: dict = {}

    def observe() -> None:
        deadline = time.monotonic() + 90
        completed_at = None
        try:
            while not stop.wait(0.1):
                rows = executions.list_executions(job_id=job["id"])
                if len(rows) > 1:
                    raise RuntimeError("multiple execution attempts; no retry")
                if rows and rows[0]["status"] in {"completed", "failed", "unknown"}:
                    if rows[0]["status"] != "completed":
                        raise RuntimeError("job did not complete; no retry")
                    completed_at = completed_at or time.monotonic()
                    # Observe subsequent real Hermes ticks without manually firing.
                    if time.monotonic() - completed_at >= 3:
                        observation["execution"] = rows[0]
                        break
                if time.monotonic() >= deadline:
                    raise TimeoutError("smoke timeout; audit only, no retry")
        except BaseException as error:
            observation["error"] = type(error).__name__ + ": " + str(error)
        finally:
            stop.set()

    observer = threading.Thread(target=observe, name="jl-smoke-observer", daemon=True)
    observer.start()
    failure = None
    try:
        HermesSchedulerService(runtime).serve(stop, interval=1)
    except BaseException as error:
        failure = type(error).__name__ + ": " + str(error)
    finally:
        stop.set()
        observer.join(timeout=5)
    rows = executions.list_executions(job_id=job["id"])
    output_paths = list((home / "cron/output" / job["id"]).glob("*.md"))
    with closing(
        sqlite3.connect(
            f"file:{home / 'jl-authorization/authorization.sqlite'}?mode=ro", uri=True
        )
    ) as db:
        receipts = db.execute(
            "SELECT execution,occurrence,state FROM receipts WHERE job=?", (job["id"],)
        ).fetchall()
        stopped = db.execute("SELECT stopped FROM settings WHERE id=1").fetchone()[0]
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    passed = (
        not failure
        and not observation.get("error")
        and observation.get("execution")
        and len(rows) == 1
        and rows[0]["status"] == "completed"
        and len(receipts) == 1
        and receipts[0] == (rows[0]["id"], rows[0]["scheduled_instant"], "spent")
        and occurrence.completed_occurrence(job, rows[0]["scheduled_instant"])
        and len(output_paths) == 1
        and output_paths[0].read_text().count("JL_REMINDER_DONE") == 1
        and consumed
        and stopped == 1
        and integrity == "ok"
        and not network_attempts
        and len(script_launches) == 1
        and not forbidden_imports
    )
    result = {
        "result": "PASS" if passed else "FAIL",
        "job_id": job["id"],
        "history": rows,
        "receipts": receipts,
        "output_files": [str(p) for p in output_paths],
        "approval_consumed": consumed,
        "global_stop_persisted": stopped == 1,
        "network_attempts": network_attempts,
        "script_launch_count": len(script_launches),
        "forbidden_imports": forbidden_imports,
        "completed_occurrence": bool(rows)
        and occurrence.completed_occurrence(job, rows[0]["scheduled_instant"]),
        "service_stopped": not runtime.scheduler_enabled,
        "error": failure or observation.get("error"),
        "no_retry": True,
    }
    save(home / "result.json", result)
    print(json.dumps(result, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
