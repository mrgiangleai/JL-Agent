"""Pinned upstream hook tests; run separately in an isolated APFS profile.

No runtime scheduler service is enabled. Individual synchronous ticks are test
drivers; all scripts are fixed local constants without tools or network.
"""

import builtins
import contextlib
import importlib
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "upstream/hermes-agent"))


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="jl-hook-test-", dir="/private/tmp"
        )
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
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
        self.config = self.home / "config.yaml"
        self.config.write_text(
            "cron:\n  execution_policy: jl\ndatabase:\n  journal_mode: delete\n"
        )
        policy = importlib.import_module("cron.execution_policy")
        executions = importlib.import_module("cron.executions")
        jobs = importlib.import_module("cron.jobs")
        scheduler = importlib.import_module("cron.scheduler")

        self.p, self.ex, self.jobs, self.s = policy, executions, jobs, scheduler
        self.scope = jobs.use_cron_store(self.home)
        self.scope.__enter__()
        self.addCleanup(self.scope.__exit__, None, None, None)
        self.acquired = 0
        self.released = 0
        self.script = b'print("JL_REMINDER_DONE")\n'
        self.expiry = 10
        self.deny = False
        self.on_acquire = lambda: None
        self.p.register_execution_policy(self.home, "jl", self)
        # Fail loudly if any forbidden path is reached, including idle cleanup.
        for name in (
            "_launch_external_cron_worker",
            "_deliver_result",
            "_deliver_crash_failure",
            "_maybe_run_worktree_maintenance",
            "_sweep_mcp_orphans",
            "_init_cron_mcp_tools",
        ):
            guard = patch.object(self.s, name, side_effect=AssertionError(name))
            guard.start()
            self.addCleanup(guard.stop)
        for target in (
            "agent.secret_scope.build_profile_secret_scope",
            "tools.terminal_scope.install_profile_terminal_scope",
        ):
            module, symbol = target.rsplit(".", 1)
            guard = patch.object(
                importlib.import_module(module),
                symbol,
                side_effect=AssertionError(target),
            )
            spy = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(spy.assert_not_called)
        original_import = builtins.__import__
        forbidden_imports = []

        def guarded_import(name, *args, **kwargs):
            if name in {"hermes_cli.env_loader", "run_agent", "tools.mcp_tool"}:
                forbidden_imports.append(name)
                raise AssertionError(name)
            return original_import(name, *args, **kwargs)

        guard = patch.object(builtins, "__import__", side_effect=guarded_import)
        guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(lambda: self.assertEqual(forbidden_imports, []))
        guard = patch.object(
            socket.socket, "connect", side_effect=AssertionError("network")
        )
        spy = guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(spy.assert_not_called)

    @contextlib.contextmanager
    def acquire(self, request):
        self.acquired += 1
        if self.deny:
            raise self.p.AuthorizationDenied("revoked")
        self.on_acquire()
        try:
            yield self.p.ExecutionLease(
                request.fingerprint, self.script, time.monotonic() + self.expiry
            )
        finally:
            self.released += 1

    def job(self, **changes):
        job = self.jobs.create_job(
            "fixed reminder",
            "every 1h",
            no_agent=True,
            script="fixed.py",
            deliver="local",
            failure_deliver="local",
            paused=True,
        )
        self.jobs.resume_job(job["id"])
        claimed = self.jobs.claim_job_for_fire(job["id"], return_job=True)
        # Adversarial input goes straight to the gate. Feeding no_agent=False
        # into upstream management first can resolve provider defaults before
        # the execution boundary under test is even called.
        claimed.update(changes)
        return claimed

    def test_real_execution_and_terminal_replay(self):
        job = self.job()
        self.assertTrue(self.s.run_one_job(job))
        self.assertEqual(self.acquired, 1)
        self.assertEqual(self.released, 1)
        result = self.ex.latest_execution(job["id"])
        self.assertEqual(result["status"], "completed")
        output = list((self.home / "cron/output" / job["id"]).glob("*.md"))
        self.assertEqual(len(output), 1)
        self.assertIn("JL_REMINDER_DONE", output[0].read_text())
        with self.assertRaises(self.p.AuthorizationDenied):
            self.s.run_one_job(dict(job, execution_id=result["id"]))
        self.assertEqual(self.acquired, 1)

    def test_direct_run_job_and_script_denied(self):
        _run_job_script = importlib.import_module(
            "cron.scheduler_script"
        )._run_job_script

        job = self.job()
        with self.assertRaises(self.p.AuthorizationDenied):
            self.s.run_job(job)
        with self.assertRaises(self.p.AuthorizationDenied):
            _run_job_script("fixed.py")
        self.assertEqual(self.acquired, 0)

    def test_revoked_job_denied_without_consuming_repeat(self):
        self.deny = True
        job = self.job()
        with self.assertRaises(self.p.AuthorizationDenied):
            self.s.run_one_job(job)
        current = self.jobs.get_job(job["id"])
        self.assertEqual(current["state"], "paused")
        self.assertEqual(current["repeat"]["completed"], 0)
        self.assertEqual(self.ex.latest_execution(job["id"])["status"], "failed")

    def test_expired_lease_denied(self):
        self.expiry = -1
        with self.assertRaises(self.p.AuthorizationDenied):
            self.s.run_one_job(self.job())
        self.assertEqual(self.released, 1)

    def test_edit_during_acquisition_denied(self):
        job = self.job()
        self.on_acquire = lambda: self.jobs.update_job(job["id"], {"prompt": "changed"})
        with self.assertRaises(self.p.AuthorizationDenied):
            self.s.run_one_job(job)

    def test_fixed_bytes_ignore_replaced_script(self):
        scripts = self.home / "scripts"
        scripts.mkdir()
        file = scripts / "fixed.py"
        file.write_text('raise RuntimeError("unapproved file")')
        self.on_acquire = lambda: file.write_text('raise RuntimeError("replaced")')
        self.assertTrue(self.s.run_one_job(self.job()))
        self.assertEqual(self.released, 1)

    def test_forbidden_shapes(self):
        for changes in (
            {"no_agent": False},
            {"deliver": "origin"},
            {"failure_deliver": "telegram"},
            {"workdir": str(ROOT)},
            {"enabled_toolsets": ["terminal"]},
            {"new_effect": True},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(self.p.AuthorizationDenied):
                    self.s.run_one_job(self.job(**changes))
        self.assertEqual(self.acquired, 0)

    def test_config_cannot_downgrade(self):
        job = self.job()
        self.config.write_text("{}")
        with self.assertRaises(self.p.AuthorizationDenied):
            self.s.run_one_job(job)
        with self.assertRaises(self.p.AuthorizationDenied):
            self.s.tick(verbose=False)

    def test_missing_policy_in_fresh_process(self):
        import subprocess

        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from cron.execution_policy import resolve_policy; resolve_policy()"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(ROOT / "upstream/hermes-agent")],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("authorization_policy_unavailable", result.stderr)

    def test_ledger_failure_blocks_acquisition(self):
        with patch.object(self.ex, "create_execution", side_effect=OSError("ledger")):
            with self.assertRaises(OSError):
                self.s.run_one_job(self.job())
        self.assertEqual(self.acquired, 0)
        with self.assertRaises(self.p.AuthorizationDenied):
            self.s.tick(verbose=False)

    def test_duplicate_config_keys_denied(self):
        self.config.write_text("cron: {execution_policy: jl}\ncron: {}\n")
        with self.assertRaises(self.p.AuthorizationDenied):
            self.p.resolve_policy()

    def test_registration_cannot_be_replaced(self):
        with self.assertRaises(self.p.AuthorizationDenied):
            self.p.register_execution_policy(self.home, "jl", self)

    def test_extra_prompt_cannot_expand_authority(self):
        with self.assertRaises(self.p.AuthorizationDenied):
            self.s.run_one_job(self.job(), extra_prompt="run a tool")
        self.assertEqual(self.acquired, 0)

    def test_pause_while_waiting_for_authority(self):
        job = self.job()
        self.on_acquire = lambda: self.jobs.pause_job(job["id"])
        with self.assertRaises(self.p.AuthorizationDenied):
            self.s.run_one_job(job)

    def test_legacy_unconfigured_profile_has_no_policy(self):
        with tempfile.TemporaryDirectory(
            prefix="jl-legacy-test-", dir="/private/tmp"
        ) as tmp:
            with patch.dict(os.environ, {"HERMES_HOME": tmp}):
                self.assertIsNone(self.p.resolve_policy())
                early = (True, "local", "local", None)
                with patch.object(
                    self.s, "_prepare_job_prompt", return_value=(early, None)
                ):
                    self.assertEqual(self.s.run_job({"id": "legacy"}), early)

    def test_cancel_before_acquisition(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(self.p.AuthorizationDenied):
            self.s.run_one_job(self.job(), cancel_event=stop)

    def test_idle_tick_has_no_housekeeping(self):
        self.assertEqual(self.s.tick(verbose=False), 0)

    def test_real_due_oneshot_tick_and_duplicate_tick(self):
        from datetime import UTC, datetime, timedelta

        job = self.jobs.create_job(
            "due reminder",
            (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            no_agent=True,
            script="fixed.py",
            deliver="local",
            failure_deliver="local",
            paused=True,
        )
        self.jobs.resume_job(job["id"])
        self.s.tick(verbose=False)
        self.assertEqual(self.ex.latest_execution(job["id"])["status"], "completed")
        self.s.tick(verbose=False)
        self.assertEqual(self.acquired, 1)

    def test_provider_path_requires_authorization(self):
        InProcessCronScheduler = importlib.import_module(
            "cron.scheduler_provider"
        ).InProcessCronScheduler

        job = self.job()
        self.jobs.update_job(job["id"], {"fire_claim": None})
        self.assertTrue(InProcessCronScheduler().fire_due(job["id"]))
        self.assertEqual(self.acquired, 1)
        self.assertEqual(self.ex.latest_execution(job["id"])["status"], "completed")

    def test_external_worker_entry_denied(self):
        with self.assertRaises(self.p.AuthorizationDenied):
            self.s._run_external_worker_payload(self.home / "absent", self.home / "ack")
        self.assertFalse((self.home / "ack").exists())

    def test_script_environment_is_minimal(self):
        os.environ["JL_TEST_CREDENTIAL"] = "must-not-inherit"
        self.script = (
            b'import os\nassert "JL_TEST_CREDENTIAL" not in os.environ\n'
            b'print("JL_REMINDER_DONE")\n'
        )
        job = self.job()
        self.assertTrue(self.s.run_one_job(job))
        self.assertEqual(self.ex.latest_execution(job["id"])["status"], "completed")


if __name__ == "__main__":
    unittest.main()
