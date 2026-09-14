from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jl_agent.control.hermes_projection import (
    CAPABILITY_SPECS,
    HERMES_REVISION,
    HERMES_VERSION,
    HermesProjection,
    HermesProjectionError,
)
from jl_agent.control.registry import HealthState

ROOT = Path(__file__).resolve().parents[1]
HERMES_ROOT = ROOT / "upstream" / "hermes-agent"


class HermesProjectionTests(unittest.TestCase):
    def test_pinned_checkout_identity_and_representative_set(self) -> None:
        result = HermesProjection(HERMES_ROOT).project()

        self.assertEqual(result.identity.version, HERMES_VERSION)
        self.assertEqual(result.identity.revision, HERMES_REVISION)
        self.assertEqual(
            {capability.id for capability in result.registry.capabilities},
            {spec.id for spec in CAPABILITY_SPECS},
        )
        self.assertTrue(all(result.availability.values()))
        for capability in result.registry.capabilities:
            self.assertEqual(capability.source.revision, HERMES_REVISION)
            self.assertEqual(capability.health.state, HealthState.UNKNOWN)
            self.assertEqual(capability.dependencies, ("core.hermes.agent",))
        computer_use = result.registry.get("core.hermes.computer-use")
        self.assertEqual(computer_use.entrypoint.address, "computer_use")
        self.assertIn("screen.capture", computer_use.permissions)
        self.assertIn("input.control", computer_use.permissions)

    def test_identity_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "hermes-agent"\nversion = "9.9.9"\n',
                encoding="utf-8",
            )
            projection = HermesProjection(
                root, revision_reader=lambda _root: HERMES_REVISION
            )

            with self.assertRaisesRegex(HermesProjectionError, "does not match"):
                projection.project()

    def test_missing_module_is_reported_without_importing_hermes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                f'[project]\nname = "hermes-agent"\nversion = "{HERMES_VERSION}"\n',
                encoding="utf-8",
            )
            projection = HermesProjection(
                root, revision_reader=lambda _root: HERMES_REVISION
            )

            result = projection.project()

        self.assertFalse(any(result.availability.values()))


if __name__ == "__main__":
    unittest.main()
