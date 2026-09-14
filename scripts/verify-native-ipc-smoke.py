#!/usr/bin/env python3
"""Deterministic Swift-to-Python protocol-v1 AF_UNIX consent smoke test."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jl_agent.control.approvals import OneTimeApprovalStore  # noqa: E402
from jl_agent.control.audit import AuditLedger  # noqa: E402
from jl_agent.control.auth import FileCredentialProvider  # noqa: E402
from jl_agent.control.codec import decode_control_request  # noqa: E402
from jl_agent.control.consent import (  # noqa: E402
    ConsentCoordinator,
    RSAPKCS1v15SHA256Verifier,
    TrustedConsentRequestHandler,
)
from jl_agent.control.control_plane import JLControlPlane  # noqa: E402
from jl_agent.control.execution import ExecutionGate  # noqa: E402
from jl_agent.control.execution_adapter import (  # noqa: E402
    HermesExecutionAdapter,
    HermesExecutionStatus,
    HermesRuntimeRequest,
    HermesRuntimeResult,
)
from jl_agent.control.hermes_projection import HermesProjection  # noqa: E402
from jl_agent.control.ipc import UnixSocketServer  # noqa: E402
from jl_agent.control.request_state import SecureControlRequestHandler  # noqa: E402
from jl_agent.control.router import (  # noqa: E402
    DeterministicModelRouter,
    RouterPolicy,
)
from jl_agent.runtime_service import JLRuntimeService, RuntimePaths  # noqa: E402


class FakeRuntime:
    def __init__(self) -> None:
        self.requests: list[HermesRuntimeRequest] = []

    def run(self, request: HermesRuntimeRequest) -> HermesRuntimeResult:
        self.requests.append(request)
        return HermesRuntimeResult(
            HermesExecutionStatus.COMPLETED, "native-ipc-smoke-ok"
        )


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: verify-native-ipc-smoke.py NATIVE_TEST_BINARY RUNTIME_ROOT"
        )
    binary = Path(sys.argv[1]).resolve()
    runtime_root = Path(sys.argv[2]).resolve()
    if not binary.is_file():
        raise SystemExit("native test binary does not exist")
    if (
        runtime_root.parent != Path("/private/tmp")
        or not runtime_root.name.startswith("jl-agent-phase4a-")
        or runtime_root.exists()
    ):
        raise SystemExit(
            "runtime root must be a new /private/tmp/jl-agent-phase4a-* path"
        )

    identifier = uuid.uuid4().hex
    service_name = f"com.jlagent.native-ipc-smoke.{identifier}"
    key_tag = f"com.jlagent.native-consent-smoke.{identifier}"
    runtime_root.mkdir(mode=0o700)
    paths = RuntimePaths.user_local(runtime_root)
    credentials = FileCredentialProvider(paths.credential)
    credentials.load_or_create()
    native_arguments = [str(runtime_root), service_name, key_tag]
    service: JLRuntimeService | None = None
    server_thread: threading.Thread | None = None
    fake_runtime = FakeRuntime()
    try:
        subprocess.run(
            [str(binary), "provision", *native_arguments],
            check=True,
            timeout=20,
        )
        approvals = OneTimeApprovalStore()
        coordinator = ConsentCoordinator()
        control_plane = JLControlPlane(
            projection=HermesProjection(ROOT / "upstream" / "hermes-agent"),
            router=DeterministicModelRouter(RouterPolicy(max_attempts=2)),
        )
        audit = AuditLedger(paths.audit)
        gate = ExecutionGate(
            control_plane=control_plane,
            approvals=approvals,
            adapter=HermesExecutionAdapter(fake_runtime),
            audit=audit,
        )
        verifier = RSAPKCS1v15SHA256Verifier.from_file(paths.consent_public_key)

        def status() -> dict[str, object]:
            return {
                "ready": True,
                "state": "ready",
                "protocol_version": 1,
                "transport": "AF_UNIX",
                "hermes_revision": "044a77b3b6af4ce16138d42762f812a20b9f7a89",
                "consent_available": True,
            }

        normal_handler = SecureControlRequestHandler(
            credentials=credentials,
            approvals=approvals,
            control_plane=control_plane,
            request_decoder=decode_control_request,
            execution_gate=gate,
            consent_coordinator=coordinator,
            status_provider=status,
            activity_reader=audit.safe_activity,
        )
        consent_handler = TrustedConsentRequestHandler(
            coordinator=coordinator,
            verifier=verifier,
            approvals=approvals,
            control_plane=control_plane,
            execution_gate=gate,
        )

        service = JLRuntimeService(
            paths=paths,
            server=UnixSocketServer(paths.socket, normal_handler),
            consent_server=UnixSocketServer(paths.consent_socket, consent_handler),
            consent_available=True,
        )
        service.start()
        server_thread = threading.Thread(target=service.serve_forever, daemon=True)
        server_thread.start()
        subprocess.run(
            [str(binary), "smoke", *native_arguments],
            check=True,
            timeout=20,
        )
        if len(fake_runtime.requests) != 1:
            raise RuntimeError("native smoke did not reach fake Hermes exactly once")
    finally:
        if service is not None:
            service.shutdown()
        if server_thread is not None:
            server_thread.join(timeout=2)
        subprocess.run(
            [str(binary), "cleanup", *native_arguments],
            check=False,
            timeout=20,
        )
        shutil.rmtree(runtime_root, ignore_errors=True)
    print("Swift client and Python runtime IPC smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
