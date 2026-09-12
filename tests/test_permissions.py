from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from jl_agent.control.permissions import (
    ActionClass,
    ActionProposal,
    DecisionOutcome,
    PermissionRiskEngine,
)
from jl_agent.control.registry import CapabilityRegistry


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "config" / "capabilities.example.yaml"


class PermissionRiskEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = PermissionRiskEngine()
        self.capability = CapabilityRegistry.from_file(FIXTURE).capabilities[0]

    def proposal(self, **changes: object) -> ActionProposal:
        values: dict[str, object] = {
            "action": "read_file",
            "normalized_arguments": {"path": "docs/README.md"},
            "requested_permissions": ("local.read",),
            "resolved_target": "/workspace/docs/README.md",
            "session": "session-1",
        }
        values.update(changes)
        return ActionProposal(**values)  # type: ignore[arg-type]

    def test_read_only_and_scoped_reversible_actions_may_proceed(self) -> None:
        read = self.engine.evaluate(self.capability, self.proposal())
        write = self.engine.evaluate(
            self.capability,
            self.proposal(
                action="write_file",
                requested_permissions=("local.write.reversible",),
            ),
        )

        self.assertEqual(read.outcome, DecisionOutcome.MAY_PROCEED)
        self.assertEqual(write.outcome, DecisionOutcome.MAY_PROCEED)

    def test_every_required_action_class_is_classified(self) -> None:
        cases = {
            "local.read": ActionClass.READ_ONLY,
            "local.write.reversible": ActionClass.REVERSIBLE_LOCAL,
            "local.delete": ActionClass.DESTRUCTIVE,
            "external.send": ActionClass.EXTERNAL_COMMUNICATION,
            "credential.use": ActionClass.CREDENTIAL_SENSITIVE,
            "finance.transact": ActionClass.FINANCIAL_HIGH_RISK,
        }
        capability = replace(self.capability, permissions=tuple(cases))

        for scope, expected_class in cases.items():
            with self.subTest(scope=scope):
                decision = self.engine.evaluate(
                    capability,
                    self.proposal(requested_permissions=(scope,)),
                )
                self.assertIn(expected_class, decision.action_classes)

    def test_high_risk_unattended_actions_are_denied(self) -> None:
        scopes = (
            "local.delete",
            "external.send",
            "credential.use",
            "finance.transact",
        )
        capability = replace(
            self.capability,
            permissions=scopes,
        )

        for scope in scopes:
            with self.subTest(scope=scope):
                decision = self.engine.evaluate(
                    capability,
                    self.proposal(requested_permissions=(scope,), unattended=True),
                )
                self.assertEqual(decision.outcome, DecisionOutcome.MUST_BE_DENIED)

    def test_confirmation_fails_closed_without_approval_surface(self) -> None:
        decision = self.engine.evaluate(
            self.capability,
            self.proposal(
                requested_permissions=("local.write.reversible",),
                target_within_workspace=False,
                approval_surface_available=False,
            ),
        )

        self.assertEqual(decision.outcome, DecisionOutcome.MUST_BE_DENIED)
        self.assertIn("approval surface is unavailable", decision.reasons)

    def test_unknown_excessive_or_upstream_denied_action_is_denied(self) -> None:
        unknown = self.engine.evaluate(
            self.capability,
            self.proposal(requested_permissions=("unknown.scope",)),
        )
        upstream = self.engine.evaluate(
            self.capability,
            self.proposal(upstream_denied=True),
        )

        self.assertEqual(unknown.outcome, DecisionOutcome.MUST_BE_DENIED)
        self.assertEqual(upstream.outcome, DecisionOutcome.MUST_BE_DENIED)

    def test_binding_is_deterministic_and_argument_sensitive(self) -> None:
        first = self.engine.evaluate(
            self.capability,
            self.proposal(normalized_arguments={"b": 2, "a": 1}),
        )
        reordered = self.engine.evaluate(
            self.capability,
            self.proposal(normalized_arguments={"a": 1, "b": 2}),
        )
        changed = self.engine.evaluate(
            self.capability,
            self.proposal(normalized_arguments={"a": 1, "b": 3}),
        )

        self.assertEqual(first.binding_fingerprint, reordered.binding_fingerprint)
        self.assertNotEqual(first.binding_fingerprint, changed.binding_fingerprint)


if __name__ == "__main__":
    unittest.main()
