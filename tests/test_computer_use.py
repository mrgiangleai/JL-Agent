from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from jl_agent.control.computer_use import (
    HermesComputerUseReadinessProbe,
    MacOSPermissionKind,
    MacOSPermissionState,
)
from jl_agent.control.registry import HealthState

ROOT = Path(__file__).resolve().parents[1]


def completed(command: list[str], payload: object, returncode: int = 0):
    return subprocess.CompletedProcess(
        command,
        returncode,
        stdout=json.dumps(payload),
        stderr="",
    )


class ComputerUseReadinessTests(unittest.TestCase):
    def probe(self, status: dict[str, object], *, manifest: object | None = None):
        manifest = manifest or {
            "binary_version": "0.20.1",
            "mcp_invocation": {"command": "cua-driver", "args": ["mcp"]},
        }

        def run(command, _timeout):
            payload = manifest if command[1] == "manifest" else status
            return completed(list(command), payload)

        return HermesComputerUseReadinessProbe(
            ROOT / "upstream" / "hermes-agent",
            platform="darwin",
            driver_resolver=lambda: "/opt/cua-driver",
            command_runner=run,
        ).inspect()

    def test_granted_permissions_make_the_audited_path_healthy(self) -> None:
        readiness = self.probe(
            {
                "accessibility": True,
                "screen_recording": True,
                "screen_recording_capturable": True,
            }
        )

        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.health_probe().state, HealthState.HEALTHY)
        self.assertEqual(
            {item.kind for item in readiness.permissions},
            {
                MacOSPermissionKind.ACCESSIBILITY,
                MacOSPermissionKind.SCREEN_RECORDING,
            },
        )

    def test_false_boolean_is_unknown_and_fails_closed(self) -> None:
        readiness = self.probe(
            {"accessibility": False, "screen_recording": True}
        )

        self.assertEqual(
            readiness.permissions[0].state, MacOSPermissionState.UNKNOWN
        )
        self.assertEqual(readiness.health_probe().state, HealthState.UNAVAILABLE)

    def test_explicit_denied_not_determined_and_restart_states_are_preserved(self) -> None:
        denied = self.probe(
            {
                "accessibility_state": "denied",
                "screen_recording_state": "notDetermined",
            }
        )
        restart = self.probe(
            {
                "accessibility": True,
                "screen_recording": True,
                "screen_recording_capturable": False,
            }
        )

        self.assertEqual(denied.permissions[0].state, MacOSPermissionState.DENIED)
        self.assertEqual(
            denied.permissions[1].state, MacOSPermissionState.NOT_DETERMINED
        )
        self.assertEqual(
            restart.permissions[1].state,
            MacOSPermissionState.RESTART_REQUIRED,
        )
        self.assertFalse(restart.ready)

    def test_missing_driver_and_bad_manifest_have_distinct_health(self) -> None:
        missing = HermesComputerUseReadinessProbe(
            ROOT / "upstream" / "hermes-agent",
            platform="darwin",
            driver_resolver=lambda: None,
        ).inspect()
        bad = self.probe(
            {"accessibility": True, "screen_recording": True},
            manifest={"binary_version": "0.19.0", "mcp_invocation": {}},
        )

        self.assertEqual(missing.health_probe().state, HealthState.UNAVAILABLE)
        self.assertEqual(bad.health_probe().state, HealthState.MISCONFIGURED)

    def test_disabled_probe_runs_no_external_command(self) -> None:
        calls = []
        readiness = HermesComputerUseReadinessProbe(
            ROOT / "upstream" / "hermes-agent",
            enabled=False,
            platform="darwin",
            driver_resolver=lambda: calls.append("resolve"),
        ).inspect()

        self.assertEqual(readiness.health_probe().state, HealthState.DISABLED)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
