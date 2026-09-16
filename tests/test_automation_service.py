"""Deterministic lifecycle tests; no live job or service installation."""

import plistlib
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jl_agent.automation_service import HermesSchedulerService, require_internal_apfs
from jl_agent.control.automation import AutomationDenied


class SchedulerServiceTests(unittest.TestCase):
    def setUp(self):
        self.authority = Mock()
        self.authority.dispatch_allowed.return_value = True
        self.runtime = Mock(home=Path("/private/tmp"), authority=self.authority)
        self.scheduler = Mock()
        self.scheduler.get_running_job_ids.return_value = []
        self.jobs = Mock()
        self.jobs.get_ticker_last_error.return_value = None
        self.hook = Mock()
        self.provider = Mock()
        self.modules = {
            "cron.scheduler": self.scheduler,
            "cron.jobs": self.jobs,
            "cron.execution_policy": self.hook,
            "cron.scheduler_provider": self.provider,
        }
        self.service = HermesSchedulerService(self.runtime)
        self.stop = threading.Event()
        for patcher in (
            patch("jl_agent.automation_service.require_internal_apfs"),
            patch(
                "jl_agent.automation_service.importlib.import_module",
                side_effect=self.modules.__getitem__,
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def start_with(self, callback):
        self.provider.InProcessCronScheduler.return_value.start.side_effect = callback
        self.service.serve(self.stop)

    def test_delegates_tick_and_stops_without_issuing_grants(self):
        def run(stop, *, interval, can_dispatch):
            self.assertEqual(interval, 60)
            self.assertTrue(self.runtime.scheduler_enabled)
            self.assertTrue(can_dispatch())
            stop.set()
            self.assertFalse(can_dispatch())

        self.start_with(run)
        self.runtime.shutdown.assert_called_once()
        self.authority.global_stop.assert_called_once()
        self.authority.activate.assert_not_called()
        self.assertFalse(self.runtime.scheduler_enabled)
        with self.assertRaisesRegex(AutomationDenied, "start_invalid"):
            self.service.serve(self.stop)

    def test_global_stop_rejects_start(self):
        self.authority.dispatch_allowed.return_value = False
        with self.assertRaisesRegex(AutomationDenied, "globally_stopped"):
            self.service.serve(self.stop)
        self.provider.InProcessCronScheduler.assert_not_called()

    def test_gate_faults_stop_without_retry(self):
        for cause in ("missing_hook", "global_stop", "ticker_error", "ledger_error"):
            with self.subTest(cause=cause):
                self.service = HermesSchedulerService(self.runtime)
                self.stop = threading.Event()
                self.authority.dispatch_allowed.side_effect = None
                self.authority.dispatch_allowed.return_value = True
                self.hook.resolve_policy.return_value = object()
                self.jobs.get_ticker_last_error.return_value = None
                self.provider.InProcessCronScheduler.return_value.start.reset_mock()

                def run(stop, *, interval, can_dispatch, cause=cause):
                    if cause == "missing_hook":
                        self.hook.resolve_policy.return_value = None
                    elif cause == "global_stop":
                        self.authority.dispatch_allowed.return_value = False
                    elif cause == "ticker_error":
                        self.jobs.get_ticker_last_error.return_value = "failed"
                    else:
                        self.authority.dispatch_allowed.side_effect = OSError("ledger")
                    self.assertFalse(can_dispatch())
                    self.assertTrue(stop.is_set())
                    self.assertFalse(can_dispatch())

                with self.assertRaisesRegex(AutomationDenied, "no_retry"):
                    self.start_with(run)
                self.provider.InProcessCronScheduler.return_value.start.assert_called_once()

    def test_stop_write_failure_still_drains_and_closes(self):
        self.authority.global_stop.side_effect = OSError("readonly")
        self.runtime.shutdown.side_effect = OSError("fault latched")
        self.scheduler.get_running_job_ids.side_effect = [["running"], []]
        with self.assertRaisesRegex(AutomationDenied, "no_retry"):
            self.start_with(lambda *a, **kw: None)
        self.authority.close.assert_called_once()
        self.assertEqual(self.scheduler.get_running_job_ids.call_count, 2)
        self.assertFalse(self.runtime.scheduler_enabled)


class APFSRequirementTests(unittest.TestCase):
    def test_only_internal_apfs_accepted(self):
        for filesystem, internal, accepted in [
            ("apfs", True, True),
            ("apfs", False, False),
            ("exfat", True, False),
        ]:
            with self.subTest(filesystem=filesystem, internal=internal):
                outputs = [
                    SimpleNamespace(
                        stdout=(
                            "Filesystem blocks Used Available Capacity Mounted on\n"
                            "/dev/disk3s5 1 1 1 1% /System/Volumes/Data\n"
                        )
                    ),
                    SimpleNamespace(
                        stdout=plistlib.dumps(
                            {"FilesystemType": filesystem, "Internal": internal}
                        )
                    ),
                ]
                with patch(
                    "jl_agent.automation_service.subprocess.run", side_effect=outputs
                ):
                    if accepted:
                        require_internal_apfs(Path("/private/tmp"))
                    else:
                        with self.assertRaises(AutomationDenied):
                            require_internal_apfs(Path("/private/tmp"))

    def test_device_lookup_failure_denies(self):
        with patch(
            "jl_agent.automation_service.subprocess.run",
            side_effect=OSError("unavailable"),
        ):
            with self.assertRaises(OSError):
                require_internal_apfs(Path("/private/tmp"))
