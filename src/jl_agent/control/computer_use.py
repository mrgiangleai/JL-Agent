"""Side-effect-free JL projection of Hermes macOS computer-use readiness."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .health import ProbeOutcome
from .registry import HealthState


class MacOSPermissionState(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"
    NOT_DETERMINED = "notDetermined"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    RESTART_REQUIRED = "restartRequired"


class MacOSPermissionKind(StrEnum):
    ACCESSIBILITY = "accessibility"
    SCREEN_RECORDING = "screenRecording"


@dataclass(frozen=True, slots=True)
class MacOSPermissionStatus:
    kind: MacOSPermissionKind
    state: MacOSPermissionState
    explanation: str
    settings_url: str

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "state": self.state.value,
            "explanation": self.explanation,
            "settings_url": self.settings_url,
        }


@dataclass(frozen=True, slots=True)
class ComputerUseReadiness:
    enabled: bool
    platform_supported: bool
    driver_available: bool
    driver_contract_ready: bool
    driver_version: str | None
    permissions: tuple[MacOSPermissionStatus, ...]
    detail: str

    @property
    def ready(self) -> bool:
        return (
            self.enabled
            and self.platform_supported
            and self.driver_available
            and self.driver_contract_ready
            and all(
                permission.state is MacOSPermissionState.GRANTED
                for permission in self.permissions
            )
        )

    def health_probe(self) -> ProbeOutcome:
        if not self.enabled:
            return ProbeOutcome(HealthState.DISABLED, "computer use is disabled")
        if not self.platform_supported:
            return ProbeOutcome(
                HealthState.UNAVAILABLE, "computer use is unavailable on this platform"
            )
        if not self.driver_available:
            return ProbeOutcome(
                HealthState.UNAVAILABLE, "cua-driver is not installed or resolvable"
            )
        if not self.driver_contract_ready:
            return ProbeOutcome(
                HealthState.MISCONFIGURED,
                self.detail or "cua-driver runtime contract is invalid",
            )
        missing = [
            permission
            for permission in self.permissions
            if permission.state is not MacOSPermissionState.GRANTED
        ]
        if missing:
            summary = ", ".join(
                f"{item.kind.value}={item.state.value}" for item in missing
            )
            return ProbeOutcome(
                HealthState.UNAVAILABLE,
                f"required macOS permissions are not ready: {summary}",
            )
        return ProbeOutcome(HealthState.HEALTHY, "driver and required TCC grants are ready")

    def as_dict(self) -> dict[str, object]:
        probe = self.health_probe()
        return {
            "enabled": self.enabled,
            "health": probe.state.value,
            "ready": self.ready,
            "platform_supported": self.platform_supported,
            "driver_available": self.driver_available,
            "driver_contract_ready": self.driver_contract_ready,
            "driver_version": self.driver_version,
            "detail": probe.detail,
            "permissions": [item.as_dict() for item in self.permissions],
        }


CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]
DriverResolver = Callable[[], str | None]


class HermesComputerUseReadinessProbe:
    """Read the audited cua-driver CLI contract without starting an MCP session."""

    def __init__(
        self,
        hermes_root: str | Path,
        *,
        enabled: bool = True,
        platform: str = sys.platform,
        driver_resolver: DriverResolver | None = None,
        command_runner: CommandRunner | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 5:
            raise ValueError("computer-use probe timeout must be within 5 seconds")
        self.hermes_root = Path(hermes_root)
        self.enabled = enabled
        self.platform = platform
        self._driver_resolver = driver_resolver
        self._command_runner = command_runner or _run_command
        self.timeout_seconds = timeout_seconds

    def inspect(self) -> ComputerUseReadiness:
        permissions = _unavailable_permissions()
        if not self.enabled:
            return ComputerUseReadiness(
                False, self.platform == "darwin", False, False, None, permissions,
                "computer use is disabled",
            )
        if self.platform != "darwin":
            return ComputerUseReadiness(
                True, False, False, False, None, permissions,
                "Phase 4B exposes only the audited macOS slice",
            )
        try:
            binary = (
                self._driver_resolver()
                if self._driver_resolver is not None
                else self._resolve_with_pinned_hermes()
            )
        except Exception as error:
            return ComputerUseReadiness(
                True, True, False, False, None, permissions,
                f"cua-driver resolution failed: {error}",
            )
        if not binary:
            return ComputerUseReadiness(
                True, True, False, False, None, permissions,
                "cua-driver is not installed or resolvable",
            )
        manifest, manifest_error = self._json_command([binary, "manifest"])
        contract_ready, version, contract_detail = _manifest_status(manifest)
        if manifest_error:
            contract_detail = manifest_error
        permission_data, permission_error = self._json_command(
            [binary, "permissions", "status", "--json"]
        )
        permissions = _permission_statuses(permission_data)
        detail = permission_error or contract_detail
        return ComputerUseReadiness(
            True,
            True,
            True,
            contract_ready and not manifest_error,
            version,
            permissions,
            detail,
        )

    def _resolve_with_pinned_hermes(self) -> str | None:
        """Use Hermes' own binary resolver; importing it performs no driver action."""
        root = str(self.hermes_root)
        if root not in sys.path:
            sys.path.insert(0, root)
        from tools.computer_use.cua_backend_driver import resolve_cua_driver_cmd

        return resolve_cua_driver_cmd()

    def _json_command(
        self, command: Sequence[str]
    ) -> tuple[Mapping[str, Any] | None, str]:
        try:
            completed = self._command_runner(command, self.timeout_seconds)
        except (OSError, subprocess.SubprocessError) as error:
            return None, f"{command[1]} probe failed: {error}"
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "probe failed").strip()
            return None, message[:300]
        try:
            value = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError):
            return None, f"{command[1]} probe returned invalid JSON"
        if not isinstance(value, dict):
            return None, f"{command[1]} probe returned a non-object"
        return value, ""


def _run_command(
    command: Sequence[str], timeout_seconds: float
) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.endswith("_API_KEY")
        and not key.endswith("_TOKEN")
        and key not in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
    }
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=timeout_seconds,
        env=env,
    )


def _manifest_status(
    manifest: Mapping[str, Any] | None,
) -> tuple[bool, str | None, str]:
    if manifest is None:
        return False, None, "cua-driver manifest is unavailable"
    version = str(manifest.get("binary_version") or "").strip() or None
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", version or "")
    if match is None or tuple(int(part) for part in match.groups()) < (0, 20, 0):
        return False, version, "Hermes computer use requires cua-driver 0.20.0 or newer"
    invocation = manifest.get("mcp_invocation")
    if not isinstance(invocation, dict) or not isinstance(invocation.get("args"), list):
        return False, version, "cua-driver manifest has no MCP invocation"
    return True, version, ""


def _permission_statuses(
    data: Mapping[str, Any] | None,
) -> tuple[MacOSPermissionStatus, ...]:
    if data is None:
        return _unavailable_permissions()
    accessibility = _permission_state(data, "accessibility")
    screen = _permission_state(data, "screen_recording")
    if screen is MacOSPermissionState.GRANTED and data.get(
        "screen_recording_capturable"
    ) is False:
        screen = MacOSPermissionState.RESTART_REQUIRED
    return (
        MacOSPermissionStatus(
            MacOSPermissionKind.ACCESSIBILITY,
            accessibility,
            "Lets CuaDriver inspect accessibility elements and deliver mouse or keyboard input.",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        ),
        MacOSPermissionStatus(
            MacOSPermissionKind.SCREEN_RECORDING,
            screen,
            "Lets CuaDriver capture window or screen pixels for computer use.",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        ),
    )


def _permission_state(
    data: Mapping[str, Any], key: str
) -> MacOSPermissionState:
    explicit = data.get(f"{key}_state")
    if isinstance(explicit, str):
        aliases = {
            "granted": MacOSPermissionState.GRANTED,
            "denied": MacOSPermissionState.DENIED,
            "notdetermined": MacOSPermissionState.NOT_DETERMINED,
            "not_determined": MacOSPermissionState.NOT_DETERMINED,
            "unknown": MacOSPermissionState.UNKNOWN,
            "unavailable": MacOSPermissionState.UNAVAILABLE,
            "restartrequired": MacOSPermissionState.RESTART_REQUIRED,
            "restart_required": MacOSPermissionState.RESTART_REQUIRED,
        }
        if state := aliases.get(explicit.replace("-", "").lower()):
            return state
    if data.get(key) is True:
        return MacOSPermissionState.GRANTED
    # The pinned driver boolean does not distinguish denied from not determined.
    if data.get(key) is False:
        return MacOSPermissionState.UNKNOWN
    return MacOSPermissionState.UNAVAILABLE


def _unavailable_permissions() -> tuple[MacOSPermissionStatus, ...]:
    return _permission_statuses({})
