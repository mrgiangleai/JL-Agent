from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jl_agent.control.auth import FileCredentialProvider
from jl_agent.runtime_service import (
    JLRuntimeService,
    RuntimePaths,
    build_runtime_service,
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


if __name__ == "__main__":
    unittest.main()
