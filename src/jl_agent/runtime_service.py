"""Foreground, user-local Phase 3B runtime service composition."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import secrets
import signal
import shutil
import stat
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from .automation_runtime import AutomationRuntime
from .control.approvals import OneTimeApprovalStore
from .control.assistant_loop import AssistantAdmission
from .control.audit import AuditLedger
from .control.auth import FileCredentialProvider
from .control.automation_management import (
    AutomationConsentRequestHandler,
    AutomationManager,
    AutomationRequestHandler,
)
from .control.codec import decode_control_request
from .control.computer_use import (
    ComputerUseExecutionReadiness,
    ComputerUseTargetGuard,
    HermesComputerUseReadinessProbe,
    MacOSForegroundApplicationProbe,
)
from .control.consent import (
    ConsentCoordinator,
    ConsentSignatureVerifier,
    RSAPKCS1v15SHA256Verifier,
    TrustedConsentRequestHandler,
    UnavailableConsentVerifier,
)
from .control.control_plane import JLControlPlane
from .control.execution import ExecutionGate
from .control.execution_adapter import HermesExecutionAdapter, HermesToolRuntime
from .control.hermes_projection import HERMES_REVISION, HermesProjection
from .control.ipc import UnixSocketServer
from .control.request_state import SecureControlRequestHandler
from .control.router import DeterministicModelRouter, RouterPolicy
from .control.skill_manager import PinnedHermesSkillsGateway, SkillManager
from .control.voice import (
    HermesTextOnlyTurnRunner,
    HermesVoiceBackend,
    VoiceCoordinator,
    macos_say_tts_config,
    voice_activation_approved_from_environment,
    voice_enabled_from_environment,
)


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    root: Path
    socket: Path
    credential: Path
    audit: Path
    readiness: Path
    consent_socket: Path
    consent_public_key: Path
    hermes: Path
    models: Path
    logs: Path

    @classmethod
    def user_local(cls, root: str | Path | None = None) -> RuntimePaths:
        if root is None:
            support = Path.home() / "Library" / "Application Support" / "JL Agent"
            base = support / "runtime"
            cache = Path.home() / "Library" / "Caches" / "JL Agent"
            logs = Path.home() / "Library" / "Logs" / "JL Agent"
        else:
            base = Path(root)
            support = base.parent
            cache = support / "cache"
            logs = support / "logs"
        return cls(
            root=base,
            socket=base / "jl-agent.sock",
            credential=base / "ipc.credential",
            audit=base / "audit.jsonl",
            readiness=base / "ready.json",
            consent_socket=base / "jl-agent-consent.sock",
            consent_public_key=base / "native-consent-public-key.der",
            hermes=support / "hermes",
            models=cache / "models",
            logs=logs,
        )


def _ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise PermissionError(f"private directory is a symlink: {path}")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    details = path.lstat()
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise PermissionError(f"private directory is unsafe: {path}")


def _validate_migratable_tree(path: Path) -> None:
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise RuntimeError(f"user-data migration source is not a directory: {path}")
    for entry in os.scandir(path):
        child = Path(entry.path)
        child_details = child.lstat()
        if stat.S_ISLNK(child_details.st_mode):
            raise RuntimeError(f"user-data migration source contains a symlink: {child}")
        if stat.S_ISDIR(child_details.st_mode):
            _validate_migratable_tree(child)
        elif not stat.S_ISREG(child_details.st_mode):
            raise RuntimeError(f"user-data migration source contains an unsupported file: {child}")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_tree_contents(source: Path, destination: Path) -> None:
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        target = destination / entry.name
        details = entry.lstat()
        if stat.S_ISDIR(details.st_mode):
            if target.exists() or target.is_symlink():
                if not target.is_dir() or target.is_symlink():
                    raise RuntimeError(f"user-data migration conflict: {target}")
            else:
                target.mkdir(mode=0o700)
            _copy_tree_contents(entry, target)
        elif stat.S_ISREG(details.st_mode):
            if target.exists() or target.is_symlink():
                if not target.is_file() or _file_digest(entry) != _file_digest(target):
                    raise RuntimeError(f"user-data migration conflict: {target}")
                continue
            shutil.copy2(entry, target)
        else:
            raise RuntimeError(f"user-data migration contains an unsupported file: {entry}")


def _harden_private_tree(path: Path) -> None:
    for entry in path.rglob("*"):
        details = entry.lstat()
        if stat.S_ISLNK(details.st_mode):
            raise RuntimeError(f"migrated user data contains a symlink: {entry}")
        if stat.S_ISDIR(details.st_mode):
            os.chmod(entry, stat.S_IMODE(details.st_mode) & 0o700 or 0o700)
        elif stat.S_ISREG(details.st_mode):
            os.chmod(entry, stat.S_IMODE(details.st_mode) & 0o700 or 0o600)
    os.chmod(path, 0o700)


def _migrate_tree_group(sources: tuple[Path, ...], destination: Path) -> str:
    existing = tuple(source for source in sources if source.exists())
    if not existing:
        _ensure_private_directory(destination)
        return "absent"
    for source in existing:
        _validate_migratable_tree(source)

    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir():
            raise RuntimeError(f"user-data migration destination is unsafe: {destination}")
        for source in existing:
            for entry in source.rglob("*"):
                if not entry.is_file():
                    continue
                target = destination / entry.relative_to(source)
                if not target.is_file() or _file_digest(entry) != _file_digest(target):
                    raise RuntimeError(f"user-data migration conflict: {target}")
        _ensure_private_directory(destination)
        return "already-present"

    staging = destination.parent / f".{destination.name}.migration-{secrets.token_hex(8)}"
    try:
        staging.mkdir(mode=0o700)
        for source in existing:
            _copy_tree_contents(source, staging)
        _harden_private_tree(staging)
        os.replace(staging, destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    _ensure_private_directory(destination)
    return "migrated"


def _write_private_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_private_legacy_file(path: Path) -> bytes:
    details = path.lstat()
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
    ):
        raise RuntimeError(f"user-data migration source is unsafe: {path}")
    return path.read_bytes()


def _merged_legacy_config(source: Path, destination: Path) -> bytes:
    try:
        legacy = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        current = yaml.safe_load(destination.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise RuntimeError(f"user-data migration config is invalid: {source}") from error
    if not isinstance(legacy, dict) or not isinstance(current, dict):
        raise RuntimeError(f"user-data migration config is not a mapping: {source}")
    overlap = set(legacy).intersection(current)
    if any(legacy[key] != current[key] for key in overlap):
        raise RuntimeError(f"user-data migration conflict: {destination}")
    merged = dict(legacy)
    merged.update(current)
    return yaml.safe_dump(merged, sort_keys=False).encode("utf-8")


def _write_private_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _migrate_legacy_hermes_files(
    source: Path | None, destination: Path
) -> str:
    if source is None or not source.exists():
        return "absent"
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError(f"user-data migration source is not a directory: {source}")

    prepared: dict[Path, bytes] = {}
    for name in ("config.yaml", "auth.json", ".env"):
        legacy_path = source / name
        if not legacy_path.exists():
            continue
        legacy_bytes = _read_private_legacy_file(legacy_path)
        target = destination / name
        if target.exists() or target.is_symlink():
            target_bytes = _read_private_legacy_file(target)
            if name == "config.yaml" and target_bytes != legacy_bytes:
                prepared[target] = _merged_legacy_config(legacy_path, target)
            elif target_bytes != legacy_bytes:
                raise RuntimeError(f"user-data migration conflict: {target}")
        else:
            prepared[target] = legacy_bytes

    for target, payload in prepared.items():
        _write_private_bytes(target, payload)
    return "migrated" if prepared else "already-present"


def prepare_library_state(
    paths: RuntimePaths, *, legacy_project_root: Path | None = None,
    legacy_hermes_home: Path | None = None,
) -> dict[str, object]:
    """Prepare private Library paths and migrate only known legacy JL state.

    Sources are copied into an atomic staging directory and are never removed.
    Any conflict or unsafe source fails closed before the runtime starts.
    """
    _ensure_private_directory(paths.root)
    _ensure_private_directory(paths.hermes.parent)
    _ensure_private_directory(paths.models.parent)
    _ensure_private_directory(paths.logs)
    legacy_model_sources = (
        (legacy_project_root / ".jl-agent" / "models",)
        if legacy_project_root is not None
        else ()
    )
    migration = {
        "schema_version": 1,
        "state": "ready",
        "hermes": _migrate_tree_group(
            (paths.root / "hermes-home", paths.root / "automation"), paths.hermes
        ),
        "legacy_hermes": _migrate_legacy_hermes_files(
            legacy_hermes_home, paths.hermes
        ),
        "models": _migrate_tree_group(legacy_model_sources, paths.models),
    }
    _write_private_json(paths.root / "storage-migration.json", migration)
    return migration


def require_internal_apfs(path: Path) -> None:
    """Fail closed unless the runtime state is on an internal APFS volume."""
    target = path.resolve()
    while not target.exists():
        target = target.parent
    try:
        result = subprocess.run(
            ["/bin/df", "-P", str(target)],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        device = result.stdout.splitlines()[-1].split()[0]
        if not device.startswith("/dev/"):
            raise RuntimeError("runtime_device_unavailable")
        info = plistlib.loads(
            subprocess.run(
                ["/usr/sbin/diskutil", "info", "-plist", device],
                check=True,
                capture_output=True,
                timeout=5,
            ).stdout
        )
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as error:
        raise RuntimeError("runtime_storage_probe_failed") from error
    if info.get("FilesystemType") != "apfs" or info.get("Internal") is not True:
        raise RuntimeError("runtime_requires_internal_apfs")


def inspect_runtime_lifecycle(paths: RuntimePaths) -> dict[str, object]:
    """Inspect the foreground runtime marker without trusting it as authorization."""
    stopped: dict[str, object] = {
        "running": False,
        "state": "stopped",
        "pid": None,
        "detail": "runtime readiness marker is absent",
    }
    try:
        marker = paths.readiness.lstat()
    except FileNotFoundError:
        return stopped
    if (
        marker.st_uid != os.geteuid()
        or not stat.S_ISREG(marker.st_mode)
        or stat.S_IMODE(marker.st_mode) != 0o600
        or marker.st_size > 4096
    ):
        return {
            **stopped,
            "state": "unsafe",
            "detail": "runtime marker is not a private owned file",
        }
    try:
        payload = json.loads(paths.readiness.read_text(encoding="utf-8"))
        pid = payload.get("pid") if isinstance(payload, dict) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pid = None
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return {
            **stopped,
            "state": "stale",
            "detail": "runtime marker has no valid PID",
        }
    try:
        socket_details = paths.socket.lstat()
        socket_ready = (
            socket_details.st_uid == os.geteuid()
            and stat.S_ISSOCK(socket_details.st_mode)
            and stat.S_IMODE(socket_details.st_mode) == 0o600
        )
        os.kill(pid, 0)
    except ProcessLookupError:
        return {
            **stopped,
            "state": "stale",
            "pid": pid,
            "detail": "runtime PID is not active",
        }
    except (OSError, PermissionError):
        socket_ready = False
    if not socket_ready:
        return {
            **stopped,
            "state": "stale",
            "pid": pid,
            "detail": "runtime socket is unavailable",
        }
    return {
        "running": True,
        "state": str(payload.get("state") or "running"),
        "pid": pid,
        "detail": "foreground JL runtime marker and socket are present",
    }


class JLRuntimeService:
    """Own a foreground Unix-socket server and explicit readiness lifecycle."""

    def __init__(
        self,
        *,
        paths: RuntimePaths,
        server: UnixSocketServer,
        consent_server: UnixSocketServer | None = None,
        consent_available: bool = False,
        voice_shutdown: Callable[[], None] | None = None,
        automation: AutomationRuntime | None = None,
        automation_manager: AutomationManager | None = None,
    ) -> None:
        self.paths = paths
        self.server = server
        self.consent_server = consent_server
        self.consent_available = consent_available
        self.voice_shutdown = voice_shutdown
        self.automation = automation
        self.automation_manager = automation_manager
        self._consent_thread: threading.Thread | None = None
        self._readiness_identity: tuple[int, int] | None = None

    def start(self) -> None:
        self.server.start()
        try:
            if self.consent_server is not None:
                self.consent_server.start()
                self._consent_thread = threading.Thread(
                    target=self.consent_server.serve_forever,
                    name="jl-agent-consent",
                    daemon=True,
                )
                self._consent_thread.start()
            self._write_readiness()
        except Exception:
            if self.consent_server is not None:
                self.consent_server.shutdown()
            self.server.shutdown()
            raise

    def serve_forever(self) -> None:
        self.server.serve_forever()

    def shutdown(self) -> None:
        automation_error: Exception | None = None
        if self.automation is not None:
            try:
                self.automation.shutdown()
            except Exception as error:
                automation_error = error
        if self.automation_manager is not None:
            try:
                self.automation_manager.close()
            except Exception as error:
                automation_error = error
        self.server.shutdown()
        if self.consent_server is not None:
            self.consent_server.shutdown()
        thread = self._consent_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        self._consent_thread = None
        if self.voice_shutdown is not None:
            self.voice_shutdown()
        self._remove_readiness()
        if automation_error is not None:
            raise automation_error

    def __enter__(self) -> JLRuntimeService:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()

    def _write_readiness(self) -> None:
        self.paths.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._recover_stale_readiness()
        payload = json.dumps(
            {
                "ready": True,
                "pid": os.getpid(),
                "protocol_version": 1,
                "transport": "AF_UNIX",
                "hermes_revision": HERMES_REVISION,
                "state": "ready" if self.consent_available else "degraded",
                "consent_available": self.consent_available,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor = os.open(
            self.paths.readiness,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        details = self.paths.readiness.stat()
        self._readiness_identity = (details.st_dev, details.st_ino)

    def _recover_stale_readiness(self) -> None:
        try:
            details = self.paths.readiness.lstat()
        except FileNotFoundError:
            return
        if (
            details.st_uid != os.geteuid()
            or not stat.S_ISREG(details.st_mode)
            or stat.S_IMODE(details.st_mode) != 0o600
        ):
            raise PermissionError("stale readiness path is not a private owned file")
        self.paths.readiness.unlink()

    def _remove_readiness(self) -> None:
        try:
            details = self.paths.readiness.lstat()
        except FileNotFoundError:
            return
        if (
            self._readiness_identity == (details.st_dev, details.st_ino)
            and details.st_uid == os.geteuid()
            and stat.S_ISREG(details.st_mode)
        ):
            self.paths.readiness.unlink()
        self._readiness_identity = None


def build_runtime_service(
    *,
    runtime_root: str | Path | None = None,
    project_root: str | Path | None = None,
) -> JLRuntimeService:
    """Compose the real local service without starting it or issuing approval."""
    root = (
        Path(project_root)
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    paths = RuntimePaths.user_local(runtime_root)
    approvals = OneTimeApprovalStore()
    consent = ConsentCoordinator()
    computer_use_probe = HermesComputerUseReadinessProbe(
        root / "upstream" / "hermes-agent"
    )
    hermes_projection = HermesProjection(root / "upstream" / "hermes-agent")
    hermes_projection.inspect_identity()
    control_plane = JLControlPlane(
        projection=hermes_projection,
        router=DeterministicModelRouter(
            RouterPolicy.from_file(root / "config" / "model-router.example.yaml")
        ),
        trusted_probe_provider=lambda capability_id: (
            computer_use_probe.inspect().health_probe()
            if capability_id == "core.hermes.computer-use"
            else None
        ),
    )
    audit = AuditLedger(paths.audit)
    adapter = HermesExecutionAdapter(
        HermesToolRuntime(root / "upstream" / "hermes-agent")
    )
    gate = ExecutionGate(
        control_plane=control_plane,
        approvals=approvals,
        adapter=adapter,
        audit=audit,
        target_validator=ComputerUseTargetGuard(
            MacOSForegroundApplicationProbe()
        ).validate,
    )
    credentials = FileCredentialProvider(paths.credential)
    runtime_credential = credentials.load_or_create()
    verifier = _load_consent_verifier(paths.consent_public_key)
    hermes_turn_runner = HermesTextOnlyTurnRunner(
        root / "upstream" / "hermes-agent"
    )
    automation_runtime = AutomationRuntime(
        root / "upstream" / "hermes-agent",
        paths.hermes,
        approvals,
    )
    automation_manager = AutomationManager(
        upstream=root / "upstream" / "hermes-agent",
        home=paths.hermes,
        approvals=approvals,
    )
    skill_manager = SkillManager(
        PinnedHermesSkillsGateway(
            root / "upstream" / "hermes-agent",
            paths.hermes,
        ),
        audit,
    )
    automation_handler = AutomationRequestHandler(
        automation_manager, lambda: automation_runtime.scheduler_enabled
    )

    def consent_status() -> tuple[bool, str | None]:
        consent_enrollment_current = _consent_enrollment_is_current(
            paths.consent_public_key,
            verifier.key_fingerprint if verifier.available else None,
        )
        return consent_enrollment_current, verifier.key_fingerprint

    def runtime_status(*, include_optional: bool) -> dict[str, object]:
        consent_enrollment_current, consent_key_fingerprint = consent_status()
        status: dict[str, object] = {
            "ready": True,
            "state": "ready" if consent_enrollment_current else "degraded",
            "runtime_pid": os.getpid(),
            "protocol_version": 1,
            "transport": "AF_UNIX",
            "hermes_revision": HERMES_REVISION,
            "consent_available": verifier.available,
            "consent_key_fingerprint": consent_key_fingerprint,
            "consent_enrollment_current": consent_enrollment_current,
            "optional_checks_loaded": include_optional,
        }
        if not include_optional:
            status.update(
                {
                    "computer_use": _deferred_computer_use_status(
                        consent_enrollment_current
                    ),
                    "automation": _deferred_automation_status(paths.hermes),
                }
            )
            return status

        computer_use = computer_use_probe.inspect()
        execution_readiness = ComputerUseExecutionReadiness(
            host=computer_use,
            hermes_pin_valid=True,
            authenticated_runtime=True,
            policy_ready=True,
            consent_ready=consent_enrollment_current,
        )
        status.update(
            {
                "computer_use": execution_readiness.as_dict(),
                "automation": automation_manager.status(
                    scheduler_enabled=automation_runtime.scheduler_enabled
                ),
            }
        )
        return status

    handler = SecureControlRequestHandler(
        credentials=credentials,
        approvals=approvals,
        control_plane=control_plane,
        request_decoder=decode_control_request,
        execution_gate=gate,
        consent_coordinator=consent,
        status_provider=lambda: runtime_status(include_optional=False),
        optional_status_provider=lambda: runtime_status(include_optional=True),
        activity_reader=audit.safe_activity,
        automation_handler=automation_handler,
        skill_handler=skill_manager,
    )
    assistant = AssistantAdmission(
        turn_runner=hermes_turn_runner,
        control_handler=handler,
        automation_handler=automation_handler,
    )
    voice = VoiceCoordinator(
        backend=HermesVoiceBackend(
            root / "upstream" / "hermes-agent",
            model_cache_root=(
                paths.models
            ),
            sherpa_manifest_path=(
                root / "config" / "models" / "sherpa-gigaspeech-kws-fp32.json"
            ),
            tts_config=macos_say_tts_config() if sys.platform == "darwin" else None,
        ),
        assistant_handler=handler,
        credential=runtime_credential,
        enabled=voice_enabled_from_environment(),
        activation_approved=voice_activation_approved_from_environment(),
        wake_phrase_path=paths.root / "wake-phrase.json",
    )
    handler.voice_handler = voice.handle
    handler.assistant_handler = assistant.handle
    consent_handler = TrustedConsentRequestHandler(
        coordinator=consent,
        verifier=verifier,
        approvals=approvals,
        control_plane=control_plane,
        execution_gate=gate,
        automation_handler=AutomationConsentRequestHandler(
            automation_manager, verifier
        ),
    )
    return JLRuntimeService(
        paths=paths,
        server=UnixSocketServer(paths.socket, handler),
        consent_server=UnixSocketServer(paths.consent_socket, consent_handler),
        consent_available=verifier.available,
        voice_shutdown=voice.shutdown,
        automation=automation_runtime,
        automation_manager=automation_manager,
    )


def _deferred_computer_use_status(consent_ready: bool) -> dict[str, object]:
    return {
        "enabled": True,
        "health": "unavailable",
        "ready": False,
        "platform_supported": sys.platform == "darwin",
        "driver_available": False,
        "driver_reachable": False,
        "driver_contract_ready": False,
        "driver_version": None,
        "driver_app_available": False,
        "driver_identity_ready": False,
        "driver_bundle_id": None,
        "driver_team_id": None,
        "hermes_pin_valid": True,
        "authenticated_runtime": True,
        "policy_ready": True,
        "consent_ready": consent_ready,
        "driver_service_required": False,
        "execution_ready": False,
        "blocked_reason": "CuaDriver readiness check is available on explicit refresh.",
        "detail": "CuaDriver readiness has not been checked.",
        "permissions": [],
    }


def _deferred_automation_status(home: Path) -> dict[str, object]:
    return {
        "available": False,
        "scheduler_enabled": False,
        "stopped": True,
        "profile_home": str(home),
        "mode": "not-checked",
    }


def _load_consent_verifier(path: Path) -> ConsentSignatureVerifier:
    try:
        return RSAPKCS1v15SHA256Verifier.from_file(path)
    except (FileNotFoundError, OSError, ValueError):
        return UnavailableConsentVerifier()


def _consent_enrollment_is_current(path: Path, trusted_fingerprint: str | None) -> bool:
    if trusted_fingerprint is None:
        return False
    try:
        details = path.lstat()
        if (
            details.st_uid != os.geteuid()
            or not stat.S_ISREG(details.st_mode)
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_size > 16 * 1024
        ):
            return False
        current = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return False
    return current == trusted_fingerprint


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the JL Agent local runtime")
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument(
        "--rotate-credential",
        action="store_true",
        help="rotate the stopped runtime's IPC credential and exit",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="inspect the foreground runtime marker and exit",
    )
    arguments = parser.parse_args(argv)
    if arguments.status:
        status = inspect_runtime_lifecycle(
            RuntimePaths.user_local(arguments.runtime_dir)
        )
        print(json.dumps(status, sort_keys=True))
        return 0 if status["running"] else 1
    if arguments.rotate_credential:
        paths = RuntimePaths.user_local(arguments.runtime_dir)
        if paths.socket.exists() or paths.consent_socket.exists():
            parser.error("stop the runtime before rotating its credential")
        FileCredentialProvider(paths.credential).rotate()
        print("JL Agent runtime credential rotated")
        return 0
    runtime_paths = RuntimePaths.user_local(arguments.runtime_dir)
    if sys.platform == "darwin":
        require_internal_apfs(runtime_paths.root)
    prepare_library_state(
        runtime_paths,
        legacy_project_root=Path(__file__).resolve().parents[2],
        legacy_hermes_home=Path.home() / ".hermes",
    )
    existing_hermes_home = os.environ.get("HERMES_HOME")
    if (
        existing_hermes_home
        and Path(existing_hermes_home).resolve() != runtime_paths.hermes.resolve()
    ):
        print(
            "JL Agent runtime startup failed: HERMES_HOME does not match the JL Library profile",
            file=sys.stderr,
        )
        return 1
    os.environ["HERMES_HOME"] = str(runtime_paths.hermes)

    service = build_runtime_service(runtime_root=arguments.runtime_dir)

    def stop(*_: object) -> None:
        service.shutdown()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        service.start()
        service.serve_forever()
    except Exception as error:
        print(f"JL Agent runtime startup failed: {error}", file=sys.stderr)
        return 1
    finally:
        service.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
