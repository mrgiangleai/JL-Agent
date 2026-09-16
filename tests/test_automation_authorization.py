"""Production JL policy against real Hermes, with no scheduler service or UI."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from jl_agent.automation_runtime import AutomationRuntime
from jl_agent.control.approvals import ApprovalError, OneTimeApprovalStore
from jl_agent.control.automation import (
    SCRIPT_NAME,
    AutomationAuthority,
    AutomationDenied,
)

ROOT = Path(__file__).resolve().parents[1]


class ProductionAutomationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(
            prefix="jl-production-policy-", dir="/private/tmp"
        )
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.env = patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(self.home),
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "TZ": "UTC",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            clear=True,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        (self.home / "config.yaml").write_text(
            "cron:\n  execution_policy: jl\ndatabase:\n  journal_mode: delete\n"
        )
        self.approval_time = [0.0]
        self.approvals = OneTimeApprovalStore(clock=lambda: self.approval_time[0])
        self.runtime = AutomationRuntime(
            ROOT / "upstream/hermes-agent", self.home, self.approvals
        )
        self.authority = self.runtime.bind_worker()
        self.addCleanup(self.authority.close)
        self.jobs = importlib.import_module("cron.jobs")
        self.scheduler = importlib.import_module("cron.scheduler")
        self.hook = importlib.import_module("cron.execution_policy")
        self.ex = importlib.import_module("cron.executions")
        scope = self.jobs.use_cron_store(self.home)
        scope.__enter__()
        self.addCleanup(scope.__exit__, None, None, None)
        self.job = self.jobs.create_job(
            "local reminder",
            "every 1h",
            script=SCRIPT_NAME,
            no_agent=True,
            deliver="local",
            failure_deliver="local",
            paused=True,
        )

    def approve(self, job=None, until=None):
        job = job or self.job
        until = until or time.time() + 600
        token = self.approvals.issue(
            binding_fingerprint=self.authority.binding(job, "owner", "session", until),
            caller_id="owner",
            session_id="session",
            ttl_seconds=30,
        )
        self.approvals.make_available(token.approval_id)
        self.authority.activate(
            job,
            owner="owner",
            session="session",
            until=until,
            approval_id=token.approval_id,
        )
        return token, until

    def claim(self):
        self.jobs.resume_job(self.job["id"])
        return self.jobs.claim_job_for_fire(self.job["id"], return_job=True)

    def test_production_allow_and_exact_approval_replay(self):
        token, until = self.approve()
        with self.assertRaises(ApprovalError):
            self.authority.activate(
                self.job,
                owner="owner",
                session="session",
                until=until,
                approval_id=token.approval_id,
            )
        claimed = self.claim()
        self.assertTrue(self.scheduler.run_one_job(claimed))
        self.assertEqual(
            self.ex.latest_execution(self.job["id"])["status"], "completed"
        )
        self.assertFalse(self.runtime.scheduler_enabled)

    def test_no_grant_denied(self):
        with self.assertRaises(self.hook.AuthorizationDenied):
            self.scheduler.run_one_job(self.claim())
        self.assertEqual(self.ex.latest_execution(self.job["id"])["status"], "failed")

    def test_expiry_denied(self):
        self.approve()
        self.authority.clock = lambda: time.time() + 1000
        with self.assertRaises(self.hook.AuthorizationDenied):
            self.scheduler.run_one_job(self.claim())

    def test_expired_activation_consent(self):
        until = time.time() + 600
        token = self.approvals.issue(
            binding_fingerprint=self.authority.binding(
                self.job, "owner", "session", until
            ),
            caller_id="owner",
            session_id="session",
            ttl_seconds=30,
        )
        self.approvals.make_available(token.approval_id)
        self.approval_time[0] = 31
        with self.assertRaises(ApprovalError):
            self.authority.activate(
                self.job,
                owner="owner",
                session="session",
                until=until,
                approval_id=token.approval_id,
            )

    def test_unissued_consent_denied(self):
        with self.assertRaises(ApprovalError):
            self.authority.activate(
                self.job,
                owner="owner",
                session="session",
                until=time.time() + 60,
                approval_id="forged",
            )

    def test_revoke_survives_restart(self):
        self.approve()
        self.authority.revoke(self.job["id"], owner="owner", session="session")
        self.authority.close()
        restarted = AutomationAuthority(
            self.home / "jl-authorization", OneTimeApprovalStore()
        )
        self.addCleanup(restarted.close)
        with self.assertRaises(AutomationDenied):
            with restarted.admit(self.claim(), "revoked-after-restart"):
                self.fail("revocation lost")

    def test_changed_config_invalidates_persisted_grant(self):
        self.approve()
        claimed = self.claim()
        (self.home / "config.yaml").write_text("cron: {execution_policy: jl}\n")
        with self.assertRaises(AutomationDenied):
            with self.authority.admit(claimed, "config-changed"):
                self.fail("config mutation allowed")

    def test_missing_ledger_never_recreates_authority(self):
        self.authority.close()
        self.authority.path.unlink()
        with self.assertRaises(AutomationDenied):
            AutomationAuthority(self.home / "jl-authorization", OneTimeApprovalStore())

    def test_inert_runtime_does_not_bind_or_create_state(self):
        inert_home = self.home / "not-started"
        runtime = AutomationRuntime(
            ROOT / "upstream/hermes-agent", inert_home, self.approvals
        )
        self.assertFalse(runtime.scheduler_enabled)
        self.assertIsNone(runtime.authority)
        self.assertFalse(inert_home.exists())
        runtime.shutdown()
        runtime.shutdown()
        self.assertFalse(inert_home.exists())

    def test_revoke_and_owner_mismatch(self):
        self.approve()
        with self.assertRaises(AutomationDenied):
            self.authority.revoke(self.job["id"], owner="other", session="session")
        self.authority.revoke(self.job["id"], owner="owner", session="session")
        with self.assertRaises(self.hook.AuthorizationDenied):
            self.scheduler.run_one_job(self.claim())

    def test_job_mutation_requires_new_approval(self):
        self.approve()
        self.jobs.update_job(self.job["id"], {"prompt": "different reminder"})
        with self.assertRaises(self.hook.AuthorizationDenied):
            self.scheduler.run_one_job(self.claim())

    def test_duplicate_claim_and_occurrence_receipt(self):
        self.approve()
        claimed = self.claim()
        self.assertFalse(self.jobs.claim_job_for_fire(self.job["id"], return_job=True))
        self.assertTrue(self.scheduler.run_one_job(claimed))
        with self.assertRaises(AutomationDenied):
            with self.authority.admit(claimed, "new-execution-same-occurrence"):
                self.fail("replayed")

    def test_global_stop_persists_after_reopen(self):
        self.approve()
        self.assertEqual(self.runtime.global_stop(), 0)
        self.authority.close()
        restarted = AutomationAuthority(
            self.home / "jl-authorization", OneTimeApprovalStore()
        )
        self.addCleanup(restarted.close)
        with self.assertRaises(AutomationDenied):
            with restarted.admit(self.claim(), "after-restart"):
                self.fail("global stop lost")

    def test_revocation_after_admission_reports_inflight(self):
        self.approve()
        claimed = self.claim()
        with self.authority.admit(claimed, "admitted"):
            self.assertEqual(
                self.authority.revoke(self.job["id"], owner="owner", session="session"),
                1,
            )
            self.assertEqual(self.runtime.global_stop(), 1)
            with self.assertRaises(AutomationDenied):
                with self.authority.admit(claimed, "second"):
                    self.fail("admission remained open")
        self.assertEqual(self.runtime.global_stop(), 0)

    def test_concurrent_admission_has_single_winner(self):
        self.approve()
        claimed = self.claim()
        barrier = threading.Barrier(2)
        outcomes = []

        def run(execution):
            barrier.wait(timeout=3)
            try:
                with self.authority.admit(claimed, execution):
                    outcomes.append("allow")
            except AutomationDenied:
                outcomes.append("deny")

        workers = [threading.Thread(target=run, args=(str(i),)) for i in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
        self.assertCountEqual(outcomes, ["allow", "deny"])

    def test_authority_ledger_failure_denies_before_script(self):
        self.approve()
        self.authority._db.execute("PRAGMA query_only=ON")
        with self.assertRaises(self.hook.AuthorizationDenied):
            self.scheduler.run_one_job(self.claim())
        self.assertEqual(list((self.home / "cron/output").rglob("*.md")), [])

    def test_exclusive_authority_owner(self):
        with self.assertRaises(BlockingIOError):
            AutomationAuthority(self.home / "jl-authorization", OneTimeApprovalStore())

    def test_finalize_ledger_failure_retains_unknown_receipt(self):
        self.approve()
        claimed = self.claim()
        with self.assertRaises(sqlite3.OperationalError):
            with self.authority.admit(claimed, "uncertain-finalization"):
                self.authority._db.execute("PRAGMA query_only=ON")
        self.authority.close()
        recovered = AutomationAuthority(
            self.home / "jl-authorization", OneTimeApprovalStore()
        )
        self.addCleanup(recovered.close)
        with self.assertRaises(AutomationDenied):
            with recovered.admit(claimed, "do-not-retry"):
                self.fail("uncertain receipt replayed")

    def test_fresh_worker_restart_uses_persisted_grant(self):
        self.approve()
        self.authority.close()
        code = """
import sys, os, importlib
from pathlib import Path
sys.path.insert(0, sys.argv[1] + '/src')
from jl_agent.automation_runtime import AutomationRuntime
from jl_agent.control.approvals import OneTimeApprovalStore
runtime = AutomationRuntime(Path(sys.argv[1])/'upstream/hermes-agent',
                            Path(os.environ['HERMES_HOME']), OneTimeApprovalStore())
runtime.bind_worker()
jobs = importlib.import_module('cron.jobs')
scheduler = importlib.import_module('cron.scheduler')
executions = importlib.import_module('cron.executions')
jobs.resume_job(sys.argv[2])
job = jobs.claim_job_for_fire(sys.argv[2], return_job=True)
assert scheduler.run_one_job(job)
assert executions.latest_execution(sys.argv[2])['status'] == 'completed'
runtime.authority.close()
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(ROOT), self.job["id"]],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with closing(
            sqlite3.connect(self.home / "jl-authorization/authorization.sqlite")
        ) as db:
            self.assertEqual(
                db.execute("SELECT state FROM receipts").fetchall(), [("spent",)]
            )

    def test_interrupted_authorization_recovers_unknown(self):
        self.approve()
        claimed = self.claim()
        self.authority.close()
        code = """
import sys, os, json
from pathlib import Path
sys.path.insert(0, sys.argv[1] + '/src')
from jl_agent.control.automation import AutomationAuthority
from jl_agent.control.approvals import OneTimeApprovalStore
a = AutomationAuthority(Path(os.environ['HERMES_HOME'])/'jl-authorization',
                        OneTimeApprovalStore())
with a.admit(json.loads(sys.argv[2]), 'crashed'):
    os._exit(0)
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(ROOT), json.dumps(claimed)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        recovered = AutomationAuthority(
            self.home / "jl-authorization", OneTimeApprovalStore()
        )
        self.addCleanup(recovered.close)
        with self.assertRaises(AutomationDenied):
            with recovered.admit(claimed, "retry-after-crash"):
                self.fail("unknown attempt replayed")
        self.assertEqual(
            recovered._db.execute("SELECT state FROM receipts").fetchone()[0], "unknown"
        )


if __name__ == "__main__":
    unittest.main()
