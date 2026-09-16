from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.phase7_smoke_observability import (
    ChildRuntimeOutput,
    capture_child_runtime_output,
    prepare_smoke_home,
    startup_failure_result,
    verify_smoke_permissions,
)


class CompletedChild:
    def __init__(self, stdout: str, stderr: str, returncode: int) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    def communicate(self) -> tuple[str, str]:
        return self.stdout, self.stderr


class Phase7SmokeObservabilityTests(unittest.TestCase):
    def test_child_stdout_and_stderr_are_retained_and_sanitized(self) -> None:
        child = CompletedChild(
            "boot: starting\ncredential=do-not-retain\n",
            "Traceback\nPermissionError: audit ledger must be private\n"
            "Bearer secret-token\n",
            1,
        )

        output = capture_child_runtime_output(child)

        self.assertEqual(output.returncode, 1)
        self.assertIn("boot: starting", output.stdout)
        self.assertIn("PermissionError: audit ledger must be private", output.stderr)
        self.assertNotIn("do-not-retain", output.stdout)
        self.assertNotIn("secret-token", output.stderr)

    def test_startup_failure_is_classified_before_cleanup(self) -> None:
        output = ChildRuntimeOutput(
            returncode=1,
            stdout="runtime import started\n",
            stderr="JL Agent runtime startup failed: missing config\n",
        )

        evidence = startup_failure_result(output)

        self.assertEqual(evidence["failure_layer"], "runtime-startup")
        self.assertEqual(
            evidence["runtime_startup_error"],
            "JL Agent runtime startup failed: missing config",
        )
        self.assertEqual(evidence["runtime_stdout"], "runtime import started\n")
        self.assertEqual(
            evidence["runtime_stderr"],
            "JL Agent runtime startup failed: missing config\n",
        )

    def test_strict_smoke_home_permissions_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jl-phase7-harness-") as root:
            home = Path(root) / "runtime"
            with patch(
                "scripts.phase7_smoke_observability.require_internal_apfs"
            ) as apfs_preflight:
                audit = prepare_smoke_home(home)
            apfs_preflight.assert_called_once_with(home)

            verify_smoke_permissions(home, audit)
            self.assertEqual(home.stat().st_mode & 0o777, 0o700)
            self.assertEqual(audit.stat().st_mode & 0o777, 0o600)

            os.chmod(audit, 0o640)
            with self.assertRaisesRegex(PermissionError, "0600"):
                verify_smoke_permissions(home, audit)


if __name__ == "__main__":
    unittest.main()
