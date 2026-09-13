from __future__ import annotations

import threading
import unittest
from dataclasses import replace
from pathlib import Path

from jl_agent.control.approvals import (
    ApprovalError,
    ApprovalState,
    OneTimeApprovalStore,
)
from jl_agent.control.permissions import ActionProposal, PermissionRiskEngine
from jl_agent.control.registry import CapabilityRegistry

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "config" / "capabilities.example.yaml"


class OneTimeApprovalStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 100.0
        self.store = OneTimeApprovalStore(clock=lambda: self.now)
        self.capability = CapabilityRegistry.from_file(FIXTURE).capabilities[0]
        self.engine = PermissionRiskEngine()
        self.proposal = ActionProposal(
            action="write_file",
            normalized_arguments={"path": "/outside/file", "content": "safe"},
            requested_permissions=("local.write.reversible",),
            resolved_target="/outside/file",
            caller="native-app",
            session="session-1",
            target_within_workspace=False,
        )
        self.fingerprint = self.engine.evaluate(
            self.capability, self.proposal
        ).binding_fingerprint

    def available_approval(self, **changes: object) -> str:
        values = {
            "binding_fingerprint": self.fingerprint,
            "caller_id": "native-app",
            "session_id": "session-1",
            "ttl_seconds": 30.0,
        }
        values.update(changes)
        issued = self.store.issue(**values)  # type: ignore[arg-type]
        self.assertEqual(issued.state, ApprovalState.ISSUED)
        available = self.store.make_available(issued.approval_id)
        self.assertEqual(available.state, ApprovalState.AVAILABLE)
        return issued.approval_id

    def consume(self, approval_id: str, **changes: str):
        values = {
            "binding_fingerprint": self.fingerprint,
            "caller_id": "native-app",
            "session_id": "session-1",
        }
        values.update(changes)
        return self.store.consume(approval_id, **values)

    def test_valid_approval_is_consumed_once_and_replay_is_rejected(self) -> None:
        approval_id = self.available_approval()

        consumed = self.consume(approval_id)

        self.assertEqual(consumed.state, ApprovalState.CONSUMED)
        with self.assertRaisesRegex(ApprovalError, "already consumed") as replay:
            self.consume(approval_id)
        self.assertEqual(replay.exception.code, "approval_replayed")

    def test_expired_and_revoked_approvals_cannot_be_consumed(self) -> None:
        expired_id = self.available_approval(ttl_seconds=1.0)
        self.now += 2.0

        with self.assertRaises(ApprovalError) as expired:
            self.consume(expired_id)
        self.assertEqual(expired.exception.code, "approval_unavailable")
        self.assertEqual(self.store.get(expired_id).state, ApprovalState.EXPIRED)

        revoked_id = self.available_approval()
        self.store.revoke(revoked_id)
        with self.assertRaises(ApprovalError) as revoked:
            self.consume(revoked_id)
        self.assertEqual(revoked.exception.code, "approval_unavailable")
        self.assertEqual(self.store.get(revoked_id).state, ApprovalState.REVOKED)

    def test_wrong_fingerprint_caller_or_session_does_not_consume(self) -> None:
        cases = {
            "fingerprint": {"binding_fingerprint": "0" * 64},
            "caller": {"caller_id": "other-caller"},
            "session": {"session_id": "other-session"},
        }
        for name, changes in cases.items():
            with self.subTest(name=name):
                approval_id = self.available_approval()
                with self.assertRaises(ApprovalError) as mismatch:
                    self.consume(approval_id, **changes)
                self.assertEqual(mismatch.exception.code, "approval_mismatch")
                self.assertEqual(
                    self.store.get(approval_id).state, ApprovalState.AVAILABLE
                )

    def test_changed_action_or_arguments_require_a_new_approval(self) -> None:
        approval_id = self.available_approval()
        changed_action = replace(self.proposal, action="move_file")
        changed_arguments = replace(
            self.proposal,
            normalized_arguments={"path": "/outside/file", "content": "changed"},
        )

        for proposal in (changed_action, changed_arguments):
            fingerprint = self.engine.evaluate(
                self.capability, proposal
            ).binding_fingerprint
            with self.assertRaises(ApprovalError) as mismatch:
                self.consume(approval_id, binding_fingerprint=fingerprint)
            self.assertEqual(mismatch.exception.code, "approval_mismatch")

    def test_concurrent_consumption_has_exactly_one_winner(self) -> None:
        approval_id = self.available_approval()
        barrier = threading.Barrier(8)
        results: list[str] = []
        results_lock = threading.Lock()

        def attempt() -> None:
            barrier.wait()
            try:
                self.consume(approval_id)
                outcome = "consumed"
            except ApprovalError as error:
                outcome = error.code
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1)

        self.assertEqual(results.count("consumed"), 1)
        self.assertEqual(results.count("approval_replayed"), 7)


if __name__ == "__main__":
    unittest.main()
