"""Foreground, user-local Phase 3B runtime service composition."""

from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

from .control.approvals import OneTimeApprovalStore
from .control.audit import AuditLedger
from .control.auth import FileCredentialProvider
from .control.codec import decode_control_request
from .control.computer_use import HermesComputerUseReadinessProbe
from .control.consent import (
    ConsentCoordinator,
    ConsentSignatureVerifier,
    RSAPKCS1v15SHA256Verifier,
    TrustedConsentRequestHandler,
    UnavailableConsentVerifier,
)
from .control.control_plane import JLControlPlane
from .control.execution import ExecutionGate
from .control.execution_adapter import HermesAIAgentRuntime, HermesExecutionAdapter
from .control.hermes_projection import HERMES_REVISION, HermesProjection
from .control.ipc import UnixSocketServer
from .control.request_state import SecureControlRequestHandler
from .control.router import DeterministicModelRouter, RouterPolicy


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    root: Path
    socket: Path
    credential: Path
    audit: Path
    readiness: Path
    consent_socket: Path
    consent_public_key: Path

    @classmethod
    def user_local(cls, root: str | Path | None = None) -> RuntimePaths:
        base = (
            Path(root)
            if root is not None
            else Path.home()
            / "Library"
            / "Application Support"
            / "JL Agent"
            / "runtime"
        )
        return cls(
            root=base,
            socket=base / "jl-agent.sock",
            credential=base / "ipc.credential",
            audit=base / "audit.jsonl",
            readiness=base / "ready.json",
            consent_socket=base / "jl-agent-consent.sock",
            consent_public_key=base / "native-consent-public-key.der",
        )


class JLRuntimeService:
    """Own a foreground Unix-socket server and explicit readiness lifecycle."""

    def __init__(
        self,
        *,
        paths: RuntimePaths,
        server: UnixSocketServer,
        consent_server: UnixSocketServer | None = None,
        consent_available: bool = False,
    ) -> None:
        self.paths = paths
        self.server = server
        self.consent_server = consent_server
        self.consent_available = consent_available
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
        self.server.shutdown()
        if self.consent_server is not None:
            self.consent_server.shutdown()
        thread = self._consent_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        self._consent_thread = None
        self._remove_readiness()

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
    control_plane = JLControlPlane(
        projection=HermesProjection(root / "upstream" / "hermes-agent"),
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
        HermesAIAgentRuntime(root / "upstream" / "hermes-agent")
    )
    gate = ExecutionGate(
        control_plane=control_plane,
        approvals=approvals,
        adapter=adapter,
        audit=audit,
    )
    credentials = FileCredentialProvider(paths.credential)
    credentials.load_or_create()
    verifier = _load_consent_verifier(paths.consent_public_key)

    def runtime_status() -> dict[str, object]:
        computer_use = computer_use_probe.inspect()
        return {
            "ready": True,
            "state": "ready" if verifier.available else "degraded",
            "protocol_version": 1,
            "transport": "AF_UNIX",
            "hermes_revision": HERMES_REVISION,
            "consent_available": verifier.available,
            "computer_use": computer_use.as_dict(),
        }

    handler = SecureControlRequestHandler(
        credentials=credentials,
        approvals=approvals,
        control_plane=control_plane,
        request_decoder=decode_control_request,
        execution_gate=gate,
        consent_coordinator=consent,
        status_provider=runtime_status,
        activity_reader=audit.safe_activity,
    )
    consent_handler = TrustedConsentRequestHandler(
        coordinator=consent,
        verifier=verifier,
        approvals=approvals,
        control_plane=control_plane,
        execution_gate=gate,
    )
    return JLRuntimeService(
        paths=paths,
        server=UnixSocketServer(paths.socket, handler),
        consent_server=UnixSocketServer(paths.consent_socket, consent_handler),
        consent_available=verifier.available,
    )


def _load_consent_verifier(path: Path) -> ConsentSignatureVerifier:
    try:
        return RSAPKCS1v15SHA256Verifier.from_file(path)
    except (FileNotFoundError, OSError, ValueError):
        return UnavailableConsentVerifier()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the JL Agent local runtime")
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument(
        "--rotate-credential",
        action="store_true",
        help="rotate the stopped runtime's IPC credential and exit",
    )
    arguments = parser.parse_args(argv)
    if arguments.rotate_credential:
        paths = RuntimePaths.user_local(arguments.runtime_dir)
        if paths.socket.exists() or paths.consent_socket.exists():
            parser.error("stop the runtime before rotating its credential")
        FileCredentialProvider(paths.credential).rotate()
        print("JL Agent runtime credential rotated")
        return 0
    service = build_runtime_service(runtime_root=arguments.runtime_dir)

    def stop(*_: object) -> None:
        service.shutdown()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        service.start()
        service.serve_forever()
    finally:
        service.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
