from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from jl_agent.control.audit import AuditLedger


class AuditLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "runtime" / "audit.jsonl"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_records_private_metadata_and_failure_category(self) -> None:
        ledger = AuditLedger(self.path)
        ledger.record(
            "execution_failed",
            request_id="request-1",
            caller_id="native-app",
            session_id="session-1",
            capability_id="core.hermes.files",
            error_category="runtime_error",
        )

        record = ledger.read()[0]
        self.assertEqual(record["event"], "execution_failed")
        self.assertEqual(record["error_category"], "runtime_error")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)

    def test_non_allowlisted_secret_fields_are_rejected_and_not_written(self) -> None:
        ledger = AuditLedger(self.path)
        secret = "sk-live-secret-value"

        with self.assertRaises(ValueError):
            ledger.record(
                "execution_started",
                request_id="request-1",
                caller_id="native-app",
                session_id="session-1",
                credential=secret,
            )

        self.assertNotIn(secret, self.path.read_text(encoding="utf-8"))

        with self.assertRaises(ValueError):
            ledger.record(
                "execution_started",
                request_id="request-1",
                caller_id="native-app",
                session_id="session-1",
                timestamp="forged",
            )

    def test_retention_keeps_only_newest_bounded_events(self) -> None:
        ledger = AuditLedger(self.path, max_events=2, max_bytes=4096)
        for index in range(5):
            ledger.record(
                "execution_completed",
                request_id=f"request-{index}",
                caller_id="native-app",
                session_id="session-1",
            )

        records = ledger.read()
        self.assertEqual(
            [record["request_id"] for record in records],
            ["request-3", "request-4"],
        )
        self.assertLessEqual(self.path.stat().st_size, 4096)

    def test_safe_activity_exposes_only_ui_allowlist(self) -> None:
        ledger = AuditLedger(self.path)
        ledger.record(
            "execution_completed",
            request_id="request-secret",
            caller_id="private-caller",
            session_id="private-session",
            capability_id="core.hermes.files",
            action_class=("read-only",),
            policy_decision="may-proceed",
            fingerprint_reference="f" * 64,
            approval_id_reference="a" * 64,
            approval_consumed=True,
            provider="private-provider",
            model="private-model",
        )

        activity = ledger.safe_activity()[0]

        self.assertEqual(
            set(activity),
            {
                "timestamp",
                "capability_id",
                "action_class",
                "policy_decision",
                "execution_status",
            },
        )
        self.assertEqual(activity["execution_status"], "completed")
        rendered = str(activity)
        for private in (
            "request-secret",
            "private-caller",
            "private-session",
            "private-provider",
            "private-model",
            "f" * 64,
            "a" * 64,
        ):
            self.assertNotIn(private, rendered)


if __name__ == "__main__":
    unittest.main()
