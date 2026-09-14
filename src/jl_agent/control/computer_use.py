"""Side-effect-free JL projection of Hermes macOS computer-use readiness."""

from __future__ import annotations

import importlib
import json
import os
import plistlib
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .health import ProbeOutcome
from .registry import HealthState

if TYPE_CHECKING:
    from .control_plane import ControlRequest


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


CUA_DRIVER_BUNDLE_ID = "com.trycua.driver"
CUA_DRIVER_TEAM_IDS = frozenset({"4YEC26S9KF", "YCK386LBJ7"})


@dataclass(frozen=True, slots=True)
class CuaDriverHostIdentity:
    app_available: bool
    signature_valid: bool
    bundle_id: str | None
    team_id: str | None
    detail: str

    @property
    def ready(self) -> bool:
        return (
            self.app_available
            and self.signature_valid
            and self.bundle_id == CUA_DRIVER_BUNDLE_ID
            and self.team_id in CUA_DRIVER_TEAM_IDS
        )


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
    driver_reachable: bool
    driver_contract_ready: bool
    driver_version: str | None
    driver_identity: CuaDriverHostIdentity
    permissions: tuple[MacOSPermissionStatus, ...]
    detail: str

    @property
    def ready(self) -> bool:
        return (
            self.enabled
            and self.platform_supported
            and self.driver_available
            and self.driver_reachable
            and self.driver_contract_ready
            and self.driver_identity.ready
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
        if not self.driver_reachable:
            return ProbeOutcome(
                HealthState.UNAVAILABLE,
                self.detail or "cua-driver does not answer its manifest probe",
            )
        if not self.driver_contract_ready:
            return ProbeOutcome(
                HealthState.MISCONFIGURED,
                self.detail or "cua-driver runtime contract is invalid",
            )
        if not self.driver_identity.ready:
            return ProbeOutcome(
                HealthState.MISCONFIGURED,
                self.driver_identity.detail
                or "CuaDriver.app signing identity is unavailable",
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
        return ProbeOutcome(
            HealthState.HEALTHY, "driver and required TCC grants are ready"
        )

    def as_dict(self) -> dict[str, object]:
        probe = self.health_probe()
        return {
            "enabled": self.enabled,
            "health": probe.state.value,
            "ready": self.ready,
            "platform_supported": self.platform_supported,
            "driver_available": self.driver_available,
            "driver_reachable": self.driver_reachable,
            "driver_contract_ready": self.driver_contract_ready,
            "driver_version": self.driver_version,
            "driver_app_available": self.driver_identity.app_available,
            "driver_identity_ready": self.driver_identity.ready,
            "driver_bundle_id": self.driver_identity.bundle_id,
            "driver_team_id": self.driver_identity.team_id,
            "detail": probe.detail,
            "permissions": [item.as_dict() for item in self.permissions],
        }


@dataclass(frozen=True, slots=True)
class ComputerUseExecutionReadiness:
    host: ComputerUseReadiness
    hermes_pin_valid: bool
    authenticated_runtime: bool
    policy_ready: bool
    consent_ready: bool

    @property
    def ready(self) -> bool:
        return (
            self.host.ready
            and self.hermes_pin_valid
            and self.authenticated_runtime
            and self.policy_ready
            and self.consent_ready
        )

    @property
    def blocked_reason(self) -> str:
        if not self.hermes_pin_valid:
            return "Hermes source does not match the required pin"
        if not self.authenticated_runtime:
            return "authenticated JL runtime status is unavailable"
        if not self.policy_ready:
            return "JL computer-use policy is unavailable"
        if not self.consent_ready:
            return "trusted native consent enrollment is unavailable"
        if not self.host.ready:
            return self.host.health_probe().detail
        return "ready for one exact policy-gated computer-use action"

    def as_dict(self) -> dict[str, object]:
        status = self.host.as_dict()
        status.update(
            {
                "hermes_pin_valid": self.hermes_pin_valid,
                "authenticated_runtime": self.authenticated_runtime,
                "policy_ready": self.policy_ready,
                "consent_ready": self.consent_ready,
                "driver_service_required": False,
                "execution_ready": self.ready,
                "blocked_reason": self.blocked_reason,
            }
        )
        return status


CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]
DriverResolver = Callable[[], str | None]
DriverIdentityInspector = Callable[[str], CuaDriverHostIdentity]


class ComputerUseTargetIntegrityError(RuntimeError):
    """Raised when a mutating request no longer targets its approved context."""


class MacOSForegroundApplicationProbe:
    """Read the frontmost app identity without Accessibility or Apple Events."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
        timeout_seconds: float = 1.0,
    ) -> None:
        self._command_runner = command_runner or _run_command
        self.timeout_seconds = timeout_seconds

    def __call__(self) -> str | None:
        try:
            front = self._command_runner(
                ["/usr/bin/lsappinfo", "front"], self.timeout_seconds
            )
        except (OSError, subprocess.SubprocessError):
            return None
        asn = (front.stdout or "").strip()
        if front.returncode != 0 or not asn or "NULL" in asn.upper():
            return None
        try:
            info = self._command_runner(
                ["/usr/bin/lsappinfo", "info", "-only", "bundleID,name", asn],
                self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if info.returncode != 0:
            return None
        text = info.stdout or ""
        for key in ("bundleID", "CFBundleIdentifier", "name", "LSDisplayName"):
            match = re.search(rf'\"?{key}\"?\s*=\s*\"([^\"]+)\"', text)
            if match:
                return match.group(1).strip()
        return None


class ComputerUseTargetGuard:
    """Require a fresh foreground identity before any Hermes input action."""

    def __init__(self, foreground_probe: Callable[[], str | None]) -> None:
        self.foreground_probe = foreground_probe

    def validate(self, request: ControlRequest) -> None:
        from .permissions import COMPUTER_USE_MUTATING_ACTIONS

        if request.capability_id != "core.hermes.computer-use":
            return
        arguments = request.action.normalized_arguments
        inner = arguments.get("action")
        if inner not in COMPUTER_USE_MUTATING_ACTIONS:
            return
        target = arguments.get("app")
        if (
            not isinstance(target, str)
            or not target.strip()
            or request.action.resolved_target != target.strip()
        ):
            raise ComputerUseTargetIntegrityError("computer-use target is not exact")
        expected = request.action.foreground_app.strip()
        observed = self.foreground_probe()
        if not expected or not observed:
            raise ComputerUseTargetIntegrityError(
                "foreground application identity is unavailable"
            )
        if expected.casefold() != observed.strip().casefold():
            raise ComputerUseTargetIntegrityError(
                "foreground application changed after preparation"
            )


class HermesComputerUseReadinessProbe:
    """Read the audited cua-driver CLI contract without starting an MCP session."""

    def __init__(
        self,
        hermes_root: str | Path,
        *,
        enabled: bool = True,
        platform: str = sys.platform,
        driver_resolver: DriverResolver | None = None,
        driver_identity_inspector: DriverIdentityInspector | None = None,
        command_runner: CommandRunner | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 5:
            raise ValueError("computer-use probe timeout must be within 5 seconds")
        self.hermes_root = Path(hermes_root)
        self.enabled = enabled
        self.platform = platform
        self._driver_resolver = driver_resolver
        self._driver_identity_inspector = driver_identity_inspector
        self._command_runner = command_runner or _run_command
        self.timeout_seconds = timeout_seconds

    def inspect(self) -> ComputerUseReadiness:
        permissions = _unavailable_permissions()
        unavailable_identity = CuaDriverHostIdentity(
            False, False, None, None, "CuaDriver.app is unavailable"
        )
        if not self.enabled:
            return ComputerUseReadiness(
                False,
                self.platform == "darwin",
                False,
                False,
                False,
                None,
                unavailable_identity,
                permissions,
                "computer use is disabled",
            )
        if self.platform != "darwin":
            return ComputerUseReadiness(
                True,
                False,
                False,
                False,
                False,
                None,
                unavailable_identity,
                permissions,
                "Phase 4C exposes only the audited macOS slice",
            )
        try:
            binary = (
                self._driver_resolver()
                if self._driver_resolver is not None
                else self._resolve_with_pinned_hermes()
            )
        except Exception as error:
            return ComputerUseReadiness(
                True,
                True,
                False,
                False,
                False,
                None,
                unavailable_identity,
                permissions,
                f"cua-driver resolution failed: {error}",
            )
        if not binary:
            return ComputerUseReadiness(
                True,
                True,
                False,
                False,
                False,
                None,
                unavailable_identity,
                permissions,
                "cua-driver is not installed or resolvable",
            )
        identity = (
            self._driver_identity_inspector(binary)
            if self._driver_identity_inspector is not None
            else _inspect_driver_identity(
                binary, self._command_runner, self.timeout_seconds
            )
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
            manifest is not None and not manifest_error,
            contract_ready and not manifest_error,
            version,
            identity,
            permissions,
            detail,
        )

    def _resolve_with_pinned_hermes(self) -> str | None:
        """Use Hermes' own binary resolver; importing it performs no driver action."""
        root = str(self.hermes_root)
        if root not in sys.path:
            sys.path.insert(0, root)
        module = importlib.import_module("tools.computer_use.cua_backend_driver")
        resolver = module.resolve_cua_driver_cmd
        return resolver()

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


def _inspect_driver_identity(
    binary: str,
    command_runner: CommandRunner,
    timeout_seconds: float,
) -> CuaDriverHostIdentity:
    try:
        executable = Path(binary).expanduser().resolve(strict=True)
    except OSError as error:
        return CuaDriverHostIdentity(
            False, False, None, None, f"cua-driver path is invalid: {error}"
        )
    app_path = next(
        (parent for parent in executable.parents if parent.name == "CuaDriver.app"),
        None,
    )
    if app_path is None:
        return CuaDriverHostIdentity(
            False,
            False,
            None,
            None,
            "resolved cua-driver is not carried by CuaDriver.app",
        )
    try:
        with (app_path / "Contents" / "Info.plist").open("rb") as stream:
            plist = plistlib.load(stream)
        bundle_id = plist.get("CFBundleIdentifier")
    except (OSError, plistlib.InvalidFileException):
        bundle_id = None
    if not isinstance(bundle_id, str):
        bundle_id = None
    try:
        verified = command_runner(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app_path)],
            timeout_seconds,
        )
        details = command_runner(
            ["/usr/bin/codesign", "-dv", "--verbose=4", str(app_path)],
            timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return CuaDriverHostIdentity(
            True, False, bundle_id, None, f"CuaDriver signature probe failed: {error}"
        )
    signature_valid = verified.returncode == 0 and details.returncode == 0
    signature_text = f"{details.stdout or ''}\n{details.stderr or ''}"
    team_match = re.search(r"^TeamIdentifier=(.+)$", signature_text, re.MULTILINE)
    team_id = team_match.group(1).strip() if team_match else None
    if bundle_id != CUA_DRIVER_BUNDLE_ID:
        detail = (
            f"CuaDriver bundle identifier is {bundle_id or 'missing'}, "
            f"expected {CUA_DRIVER_BUNDLE_ID}"
        )
    elif not signature_valid:
        detail = "CuaDriver.app signature verification failed"
    elif team_id not in CUA_DRIVER_TEAM_IDS:
        detail = f"CuaDriver.app signing team is {team_id or 'missing'}"
    else:
        detail = "CuaDriver.app has the expected signed host identity"
    return CuaDriverHostIdentity(
        True, signature_valid, bundle_id, team_id, detail
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
    if (
        not isinstance(invocation, dict)
        or not isinstance(invocation.get("args"), list)
        or not all(isinstance(item, str) for item in invocation["args"])
    ):
        return False, version, "cua-driver manifest has no MCP invocation"
    advertised = {
        command["name"]: {
            argument["name"]
            for argument in command.get("args", [])
            if isinstance(argument, dict) and isinstance(argument.get("name"), str)
        }
        for command in manifest.get("subcommands", [])
        if isinstance(command, dict) and isinstance(command.get("name"), str)
    }
    required = {
        "mcp": {"--socket", "--grant"},
        "serve": {
            "--socket",
            "--permission-mode",
            "--capability-manifest",
            "--approve-capability-manifest",
            "--embedded",
        },
        "stop": {"--socket"},
    }
    missing = [
        f"{command} {argument}"
        for command, arguments in required.items()
        for argument in sorted(arguments - advertised.get(command, set()))
    ]
    if missing:
        return False, version, "cua-driver manifest is missing: " + ", ".join(missing)
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
            "Lets CuaDriver inspect accessibility elements and deliver mouse or "
            "keyboard input.",
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
