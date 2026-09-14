from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from jl_agent.control.computer_use import (
    ComputerUseExecutionReadiness,
    ComputerUseTargetGuard,
    ComputerUseTargetIntegrityError,
    CuaDriverHostIdentity,
    HermesComputerUseReadinessProbe,
    MacOSPermissionKind,
    MacOSPermissionState,
)
from jl_agent.control.control_plane import ControlRequest
from jl_agent.control.permissions import ActionProposal
from jl_agent.control.registry import HealthState
from jl_agent.control.router import RouteRequest, TaskCategory

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
            "subcommands": [
                {"name": "mcp", "args": [{"name": "--socket"}, {"name": "--grant"}]},
                {
                    "name": "serve",
                    "args": [
                        {"name": "--socket"},
                        {"name": "--permission-mode"},
                        {"name": "--capability-manifest"},
                        {"name": "--approve-capability-manifest"},
                        {"name": "--embedded"},
                    ],
                },
                {"name": "stop", "args": [{"name": "--socket"}]},
            ],
        }

        def run(command, _timeout):
            payload = manifest if command[1] == "manifest" else status
            return completed(list(command), payload)

        return HermesComputerUseReadinessProbe(
            ROOT / "upstream" / "hermes-agent",
            platform="darwin",
            driver_resolver=lambda: "/opt/cua-driver",
            driver_identity_inspector=lambda _: CuaDriverHostIdentity(
                True,
                True,
                "com.trycua.driver",
                "YCK386LBJ7",
                "official identity",
            ),
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

    def test_explicit_denied_and_restart_states_are_preserved(self) -> None:
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

    def test_missing_or_wrong_driver_app_identity_fails_closed(self) -> None:
        def inspect(identity: CuaDriverHostIdentity):
            return HermesComputerUseReadinessProbe(
                ROOT / "upstream" / "hermes-agent",
                platform="darwin",
                driver_resolver=lambda: "/opt/cua-driver",
                driver_identity_inspector=lambda _: identity,
                command_runner=lambda command, _timeout: completed(
                    list(command),
                    (
                        {
                            "binary_version": "0.28.0",
                            "mcp_invocation": {"args": ["mcp"]},
                            "subcommands": [
                                {
                                    "name": "mcp",
                                    "args": [
                                        {"name": "--socket"},
                                        {"name": "--grant"},
                                    ],
                                },
                                {"name": "serve", "args": [
                                    {"name": "--socket"}, {"name": "--permission-mode"},
                                    {"name": "--capability-manifest"},
                                    {"name": "--approve-capability-manifest"},
                                    {"name": "--embedded"},
                                ]},
                                {"name": "stop", "args": [{"name": "--socket"}]},
                            ],
                        }
                        if command[1] == "manifest"
                        else {"accessibility": True, "screen_recording": True}
                    ),
                ),
            ).inspect()

        missing = inspect(CuaDriverHostIdentity(False, False, None, None, "missing"))
        wrong = inspect(
            CuaDriverHostIdentity(
                True, True, "com.trycua.driver", "UNTRUSTED", "wrong team"
            )
        )

        self.assertFalse(missing.ready)
        self.assertEqual(missing.health_probe().state, HealthState.MISCONFIGURED)
        self.assertFalse(wrong.ready)
        self.assertIn("wrong team", wrong.health_probe().detail)

    def test_execution_readiness_requires_consent_and_trusted_runtime(self) -> None:
        host = self.probe(
            {"accessibility": True, "screen_recording": True}
        )
        blocked = ComputerUseExecutionReadiness(
            host=host,
            hermes_pin_valid=True,
            authenticated_runtime=True,
            policy_ready=True,
            consent_ready=False,
        )

        self.assertFalse(blocked.ready)
        self.assertIn("consent enrollment", blocked.blocked_reason)
        self.assertFalse(blocked.as_dict()["execution_ready"])

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

    def test_target_guard_rejects_missing_or_changed_foreground_context(self) -> None:
        request = ControlRequest(
            capability_id="core.hermes.computer-use",
            action=ActionProposal(
                action="computer_use",
                normalized_arguments={"action": "click", "app": "com.apple.TextEdit"},
                requested_permissions=("input.control",),
                resolved_target="com.apple.TextEdit",
                foreground_app="com.jlagent.control",
            ),
            route=RouteRequest(
                category=TaskCategory.SIMPLE, required_abilities=frozenset()
            ),
            candidates=(),
        )
        ComputerUseTargetGuard(lambda: "com.jlagent.control").validate(request)

        with self.assertRaises(ComputerUseTargetIntegrityError):
            ComputerUseTargetGuard(lambda: "com.apple.Safari").validate(request)
        with self.assertRaises(ComputerUseTargetIntegrityError):
            ComputerUseTargetGuard(lambda: None).validate(request)

    def test_target_guard_does_not_probe_for_passive_capture(self) -> None:
        calls = []
        request = ControlRequest(
            capability_id="core.hermes.computer-use",
            action=ActionProposal(
                action="computer_use",
                normalized_arguments={"action": "capture", "mode": "ax"},
                requested_permissions=("screen.capture",),
            ),
            route=RouteRequest(
                category=TaskCategory.SIMPLE, required_abilities=frozenset()
            ),
            candidates=(),
        )

        ComputerUseTargetGuard(lambda: calls.append("probe")).validate(request)

        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
