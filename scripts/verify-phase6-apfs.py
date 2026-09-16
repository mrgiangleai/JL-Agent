#!/usr/bin/env python3
"""Real Hermes ledger/claim restart probe. Never starts a scheduler or script."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def worker(mode):
    sys.path.insert(0, str(ROOT / "upstream/hermes-agent"))
    from cron import executions as ex
    from cron import jobs
    from cron.occurrences import completed_occurrence

    home = Path(os.environ["HERMES_HOME"])
    state_path = home / "probe.json"
    if mode == "prepare":
        job = jobs.create_job(
            "local fixture",
            "every 1h",
            no_agent=True,
            script="never-executed.py",
            deliver="local",
            failure_deliver="local",
            paused=True,
        )
        assert jobs.claim_job_for_fire(job["id"]) is False
        jobs.resume_job(job["id"])
        claim = jobs.claim_job_for_fire(job["id"], return_job=True)
        assert isinstance(claim, dict)
        state = {"job": job["id"], "instant": claim["_scheduled_instant"]}
        state_path.write_text(json.dumps(state))
        child("duplicate")
        done = ex.create_execution(
            job["id"],
            source="apfs-probe",
            scheduled_instant=state["instant"],
        )
        assert ex.mark_execution_running(done["id"])
        assert ex.mark_execution_running(done["id"]) is None
        assert ex.finish_execution(done["id"], success=True)
        assert ex.finish_execution(done["id"], success=False) is None
        assert completed_occurrence(job, state["instant"])
        jobs.pause_job(job["id"])
        pending = ex.create_execution(job["id"], source="apfs-probe")
        running = ex.create_execution(job["id"], source="apfs-probe")
        assert ex.mark_execution_running(running["id"])
        state.update(done=done["id"], pending=pending["id"], running=running["id"])
        state_path.write_text(json.dumps(state))
        # Simulate loss of this owner after durable writes, without normal cleanup.
        os._exit(0)
    state = json.loads(state_path.read_text())
    if mode == "duplicate":
        assert jobs.claim_job_for_fire(state["job"], return_job=True) is False
        return
    assert jobs.get_job(state["job"])["state"] == "paused"
    assert jobs.claim_job_for_fire(state["job"]) is False
    assert completed_occurrence({"id": state["job"]}, state["instant"])
    assert ex.recover_interrupted_executions() == (2 if mode == "recover" else 0)
    assert ex.get_execution(state["done"])["status"] == "completed"
    for key in ("pending", "running"):
        assert ex.get_execution(state[key])["status"] == "unknown"
    assert len(ex.list_executions(job_id=state["job"])) == 3
    assert jobs.get_job(state["job"])["state"] == "paused"
    with sqlite3.connect(home / "cron/executions.db") as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)


def child(mode):
    subprocess.run([sys.executable, "-B", __file__, mode], check=True, timeout=30)


def main():
    if len(sys.argv) > 1:
        worker(sys.argv[1])
        return
    # Internal APFS scratch runtime, explicitly authorized for this probe.
    with tempfile.TemporaryDirectory(
        prefix="jl-phase6-runtime-", dir="/private/tmp"
    ) as tmp:
        home = Path(tmp)
        os.environ.clear()
        os.environ.update(
            HERMES_HOME=str(home),
            TZ="UTC",
            PYTHONDONTWRITEBYTECODE="1",
            PATH="/usr/bin:/bin:/usr/sbin:/sbin",
        )
        (home / "config.yaml").write_text("database:\n  journal_mode: delete\n")
        child("prepare")
        child("recover")
        child("verify")
        print(
            json.dumps(
                {
                    "result": "PASS",
                    "sqlite": sqlite3.sqlite_version,
                    "runtime_home": str(home),
                    "journal": "DELETE",
                    "checks": [
                        "paused creation",
                        "cross-process duplicate claim",
                        "terminal immutability",
                        "persisted completed occurrence",
                        "dead-owner claimed/running recovery to unknown",
                        "second restart idempotence",
                        "pause persists",
                        "integrity_check",
                    ],
                    "limits": "No scheduled effects or power-loss durability tested",
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
