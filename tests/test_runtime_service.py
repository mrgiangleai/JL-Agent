from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path

from jl_agent.control.auth import FileCredentialProvider
from jl_agent.runtime_service import (
    JLRuntimeService,
    RuntimePaths,
    _consent_enrollment_is_current,
    build_runtime_service,
    inspect_runtime_lifecycle,
    main,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeServer:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def serve_forever(self) -> None:
        return

    def shutdown(self) -> None:
        self.stopped = True


class RuntimeServiceLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.paths = RuntimePaths.user_local(Path(self.temporary.name) / "runtime")
        self.server = FakeServer()
        self.service = JLRuntimeService(
            paths=self.paths,
            server=self.server,  # type: ignore[arg-type]
        )

    def tearDown(self) -> None:
        self.service.shutdown()
        self.temporary.cleanup()

    def test_startup_readiness_and_graceful_shutdown(self) -> None:
        self.service.start()
        ready = json.loads(self.paths.readiness.read_text(encoding="utf-8"))
        self.assertTrue(self.server.started)
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["transport"], "AF_UNIX")

        self.service.shutdown()
        self.assertTrue(self.server.stopped)
        self.assertFalse(self.paths.readiness.exists())

    def test_private_stale_readiness_is_recovered(self) -> None:
        self.paths.root.mkdir(mode=0o700)
        self.paths.readiness.write_text('{"ready":true}', encoding="utf-8")
        self.paths.readiness.chmod(0o600)

        self.service.start()

        ready = json.loads(self.paths.readiness.read_text(encoding="utf-8"))
        self.assertIn("hermes_revision", ready)

    def test_production_composition_stays_user_local_without_starting(self) -> None:
        composed = build_runtime_service(
            runtime_root=Path(self.temporary.name) / "composed",
            project_root=ROOT,
        )
        self.assertEqual(composed.server.family.name, "AF_UNIX")
        self.assertEqual(composed.paths.socket.parent, composed.paths.root)
        self.assertTrue(composed.paths.credential.exists())
        self.assertTrue(composed.paths.audit.exists())
        self.assertIsNotNone(composed.server.handler.voice_handler)  # type: ignore[attr-defined]
        composed.shutdown()

    def test_stopped_runtime_credential_can_rotate_without_exposure(self) -> None:
        root = Path(self.temporary.name) / "rotation"
        provider = FileCredentialProvider(root / "ipc.credential")
        original = provider.load_or_create()

        self.assertEqual(
            main(["--runtime-dir", str(root), "--rotate-credential"]), 0
        )

        rotated = provider.load_or_create()
        self.assertNotEqual(rotated, original)
        self.assertTrue(provider.authenticate(rotated))

    def test_consent_enrollment_drift_fails_closed(self) -> None:
        enrolled = self.paths.root / "native-consent-public-key.der"
        enrolled.parent.mkdir(mode=0o700)
        enrolled.write_bytes(b"public-key-a")
        enrolled.chmod(0o600)

        fingerprint = hashlib.sha256(b"public-key-a").hexdigest()
        self.assertTrue(_consent_enrollment_is_current(enrolled, fingerprint))
        enrolled.write_bytes(b"public-key-b")
        self.assertFalse(_consent_enrollment_is_current(enrolled, fingerprint))

    def test_runtime_lifecycle_inspection_distinguishes_running_and_stale(self) -> None:
        self.paths.root.mkdir(mode=0o700)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.paths.socket))
        self.paths.socket.chmod(0o600)
        self.paths.readiness.write_text(
            json.dumps({"pid": os.getpid(), "state": "ready"}), encoding="utf-8"
        )
        self.paths.readiness.chmod(0o600)
        try:
            running = inspect_runtime_lifecycle(self.paths)
            self.assertTrue(running["running"])
            self.assertEqual(running["pid"], os.getpid())
        finally:
            listener.close()
            self.paths.socket.unlink()

        stale = inspect_runtime_lifecycle(self.paths)
        self.assertFalse(stale["running"])
        self.assertEqual(stale["state"], "stale")


if __name__ == "__main__":
    unittest.main()
