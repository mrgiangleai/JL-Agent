from __future__ import annotations

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path

from jl_agent.control.approvals import OneTimeApprovalStore
from jl_agent.control.automation_management import (
    AutomationConsentRequestHandler,
    AutomationManager,
    AutomationRequestHandler,
)
from jl_agent.control.ipc import PROTOCOL_VERSION, IPCRequestEnvelope

ROOT = Path(__file__).resolve().parents[1]


class DigestVerifier:
    available = True
    key_fingerprint = "0" * 64

    def verify(self, message: bytes, signature: str) -> bool:
        expected = base64.b64encode(hashlib.sha256(message).digest()).decode()
        return signature == expected


class AutomationManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name) / "automation"
        self.approvals = OneTimeApprovalStore()
        self.manager = AutomationManager(
            upstream=ROOT / "upstream/hermes-agent",
            home=self.home,
            approvals=self.approvals,
            clock=lambda: 1000.0,
        )
        self.handler = AutomationRequestHandler(
            self.manager, scheduler_enabled=lambda: False
        )
        self.consent = AutomationConsentRequestHandler(
            self.manager, DigestVerifier()
        )

    def tearDown(self) -> None:
        self.manager.close()
        self.temporary.cleanup()

    def envelope(
        self,
        operation: str,
        payload: dict[str, object] | None = None,
        *,
        request_id: str = "request-1",
        caller_id: str = "native-app",
        session_id: str = "session-1",
    ) -> IPCRequestEnvelope:
        return IPCRequestEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id=request_id,
            caller_id=caller_id,
            session_id=session_id,
            operation=operation,
            payload=payload or {},
            credential="credential",
        )

    def decision(
        self, challenge: dict[str, object], decision: str = "approve"
    ) -> IPCRequestEnvelope:
        message = bytearray(b"jl-agent-consent-v1\0")
        for field in (
            str(challenge["consent_id"]),
            str(challenge["request_id"]),
            str(challenge["caller_id"]),
            str(challenge["session_id"]),
            decision,
            str(challenge["nonce"]),
        ):
            encoded = field.encode()
            message.extend(len(encoded).to_bytes(8, "big"))
            message.extend(encoded)
        signature = base64.b64encode(hashlib.sha256(message).digest()).decode()
        return self.envelope(
            "automation-consent-decision",
            {
                "consent_id": challenge["consent_id"],
                "decision": decision,
                "signature": signature,
            },
            request_id=str(challenge["request_id"]),
            caller_id=str(challenge["caller_id"]),
            session_id=str(challenge["session_id"]),
        )

    def create_job(self) -> dict[str, object]:
        created = self.handler(
            self.envelope(
                "automation-create",
                {"name": "Hydrate", "schedule": "in 30m", "note": ""},
            )
        )
        self.assertTrue(created.ok)
        assert created.result is not None
        job = created.result["job"]
        assert isinstance(job, dict)
        return job

    def test_create_paused_and_activation_needs_signed_consent(self) -> None:
        job = self.create_job()
        self.assertFalse(job["enabled"])
        self.assertEqual(job["state"], "paused")

        activation = self.handler(
            self.envelope(
                "automation-resume",
                {"job_id": job["id"]},
                request_id="activate-1",
            )
        )
        self.assertTrue(activation.ok)
        assert activation.result is not None
        challenge = activation.result["consent"]
        assert isinstance(challenge, dict)

        approved = self.consent(self.decision(challenge))
        self.assertIsNotNone(approved)
        assert approved is not None
        self.assertTrue(approved.ok)
        assert approved.result is not None
        self.assertEqual(approved.result["state"], "activated")
        activated = approved.result["job"]
        assert isinstance(activated, dict)
        self.assertTrue(activated["enabled"])

        replay = self.consent(self.decision(challenge))
        self.assertIsNotNone(replay)
        assert replay is not None
        self.assertEqual(replay.error_code, "consent_replayed")

    def test_pause_remove_and_stop_all_do_not_execute_jobs(self) -> None:
        job = self.create_job()
        activation = self.handler(
            self.envelope(
                "automation-resume",
                {"job_id": job["id"]},
                request_id="activate-2",
            )
        )
        assert activation.result is not None
        challenge = activation.result["consent"]
        assert isinstance(challenge, dict)
        approved = self.consent(self.decision(challenge))
        assert approved is not None and approved.ok

        paused = self.handler(
            self.envelope("automation-pause", {"job_id": job["id"]})
        )
        self.assertTrue(paused.ok)
        assert paused.result is not None
        paused_job = paused.result["job"]
        assert isinstance(paused_job, dict)
        self.assertFalse(paused_job["enabled"])

        removed = self.handler(
            self.envelope("automation-remove", {"job_id": job["id"]})
        )
        self.assertTrue(removed.ok)
        assert removed.result is not None
        self.assertTrue(removed.result["removed"])

        stopped = self.handler(self.envelope("automation-stop-all"))
        self.assertTrue(stopped.ok)
        status = self.handler(self.envelope("automation-status"))
        self.assertTrue(status.ok)
        assert status.result is not None
        self.assertTrue(status.result["stopped"])
        self.assertFalse(status.result["scheduler_enabled"])

    def test_malformed_activation_and_wrong_signature_fail_closed(self) -> None:
        job = self.create_job()
        activation = self.handler(
            self.envelope(
                "automation-resume",
                {"job_id": job["id"]},
                request_id="activate-3",
            )
        )
        assert activation.result is not None
        challenge = activation.result["consent"]
        assert isinstance(challenge, dict)

        bad = self.decision(challenge)
        bad = self.envelope(
            "automation-consent-decision",
            {
                "consent_id": bad.payload["consent_id"],
                "decision": "approve",
                "signature": "not-valid",
            },
            request_id=bad.request_id,
        )
        denied = self.consent(bad)
        self.assertIsNotNone(denied)
        assert denied is not None
        self.assertEqual(denied.error_code, "invalid_consent_signature")


if __name__ == "__main__":
    unittest.main()
