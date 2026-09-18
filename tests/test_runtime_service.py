from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from jl_agent.control.auth import FileCredentialProvider
from jl_agent.runtime_service import (
    JLRuntimeService,
    RuntimePaths,
    _consent_enrollment_is_current,
    build_runtime_service,
    inspect_runtime_lifecycle,
    main,
    _ensure_native_voice_stt_auto_detect,
    prepare_library_state,
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
    def test_automation_fault_does_not_prevent_service_shutdown(self) -> None:
        automation = Mock()
        automation.shutdown.side_effect = RuntimeError("authority unavailable")
        self.service.automation = automation
        try:
            with self.assertRaises(RuntimeError):
                self.service.shutdown()
            self.assertTrue(self.server.stopped)
        finally:
            self.service.automation = None

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

    def test_native_voice_stt_defaults_to_hermes_auto_detect(self) -> None:
        self.paths.hermes.mkdir(parents=True, mode=0o700)
        config = self.paths.hermes / "config.yaml"
        config.write_text(
            "model:\n  provider: openai-codex\n", encoding="utf-8"
        )

        self.assertEqual(_ensure_native_voice_stt_auto_detect(self.paths.hermes), "auto-written")
        self.assertIn("language: ''", config.read_text(encoding="utf-8"))

    def test_native_voice_stt_preserves_explicit_language(self) -> None:
        self.paths.hermes.mkdir(parents=True, mode=0o700)
        config = self.paths.hermes / "config.yaml"
        config.write_text("stt:\n  language: en\n", encoding="utf-8")

        self.assertEqual(_ensure_native_voice_stt_auto_detect(self.paths.hermes), "explicit")
        self.assertEqual(config.read_text(encoding="utf-8"), "stt:\n  language: en\n")

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
        self.assertIsNotNone(composed.automation)
        assert composed.automation is not None
        self.assertFalse(composed.automation.scheduler_enabled)
        self.assertIsNone(composed.automation.authority)
        self.assertFalse(composed.automation.home.exists())
        self.assertTrue(composed.paths.credential.exists())
        self.assertTrue(composed.paths.audit.exists())
        self.assertIsNotNone(composed.server.handler.voice_handler)  # type: ignore[attr-defined]
        self.assertIsNotNone(
            composed.server.handler.assistant_handler  # type: ignore[attr-defined]
        )
        composed.shutdown()

    def test_library_layout_migrates_known_state_without_removing_sources(self) -> None:
        legacy_root = Path(self.temporary.name) / "checkout"
        legacy_models = legacy_root / ".jl-agent" / "models"
        legacy_models.mkdir(parents=True, mode=0o700)
        self.paths.root.mkdir(mode=0o700)
        self.paths.root.chmod(0o700)
        (legacy_models / "model.bin").write_bytes(b"model")
        (self.paths.root / "hermes-home").mkdir(parents=True, mode=0o700)
        (self.paths.root / "hermes-home" / "config.yaml").write_text("profile\n")
        (self.paths.root / "automation").mkdir(parents=True, mode=0o700)
        (self.paths.root / "automation" / "jobs.json").write_text("[]\n")

        migration = prepare_library_state(
            self.paths,
            legacy_project_root=legacy_root,
            legacy_hermes_home=Path(self.temporary.name) / "missing-hermes",
        )

        self.assertEqual(migration["hermes"], "migrated")
        self.assertEqual(migration["models"], "migrated")
        self.assertEqual(
            (self.paths.hermes / "config.yaml").read_text(), "profile\n"
        )
        self.assertEqual((self.paths.hermes / "jobs.json").read_text(), "[]\n")
        self.assertEqual((self.paths.models / "model.bin").read_bytes(), b"model")
        self.assertTrue((self.paths.root / "hermes-home").exists())
        self.assertTrue((self.paths.root / "automation").exists())
        self.assertTrue(legacy_models.exists())
        self.assertEqual(
            (self.paths.root / "storage-migration.json").stat().st_mode & 0o777,
            0o600,
        )

    def test_library_layout_fails_closed_on_model_migration_conflict(self) -> None:
        legacy_root = Path(self.temporary.name) / "checkout"
        legacy_models = legacy_root / ".jl-agent" / "models"
        legacy_models.mkdir(parents=True, mode=0o700)
        (legacy_models / "model.bin").write_bytes(b"legacy")
        self.paths.models.mkdir(parents=True, mode=0o700)
        self.paths.models.parent.chmod(0o700)
        self.paths.root.mkdir(mode=0o700)
        self.paths.root.chmod(0o700)
        (self.paths.models / "model.bin").write_bytes(b"different")

        with self.assertRaisesRegex(RuntimeError, "migration conflict"):
            prepare_library_state(
                self.paths,
                legacy_project_root=legacy_root,
                legacy_hermes_home=Path(self.temporary.name) / "missing-hermes",
            )

        self.assertEqual((legacy_models / "model.bin").read_bytes(), b"legacy")

    def test_legacy_hermes_auth_conflict_keeps_existing_destination(self) -> None:
        legacy = Path(self.temporary.name) / "legacy-hermes"
        legacy.mkdir(mode=0o700)
        (legacy / "auth.json").write_text('{"token":"legacy"}\n')
        self.paths.hermes.mkdir(parents=True, mode=0o700)
        (self.paths.hermes / "auth.json").write_text('{"token":"current"}\n')

        migration = prepare_library_state(
            self.paths, legacy_hermes_home=legacy
        )

        self.assertEqual(migration["legacy_hermes"], "already-present")
        self.assertEqual(
            (self.paths.hermes / "auth.json").read_text(), '{"token":"current"}\n'
        )

    def test_legacy_hermes_profile_is_forwarded_without_overwriting_jl_config(self) -> None:
        legacy = Path(self.temporary.name) / "legacy-hermes"
        legacy.mkdir(mode=0o700)
        (legacy / "config.yaml").write_text(
            "model:\n  provider: openai-codex\n  default: gpt-5.6-sol\n",
            encoding="utf-8",
        )
        (legacy / "auth.json").write_text('{"active_provider":"openai-codex"}\n')
        self.paths.hermes.mkdir(parents=True, mode=0o700)
        (self.paths.hermes / "config.yaml").write_text(
            "cron:\n  execution_policy: jl\n", encoding="utf-8"
        )

        migration = prepare_library_state(
            self.paths, legacy_hermes_home=legacy
        )

        self.assertEqual(migration["legacy_hermes"], "migrated")
        config = (self.paths.hermes / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("provider: openai-codex", config)
        self.assertIn("execution_policy: jl", config)
        self.assertEqual(
            (self.paths.hermes / "auth.json").read_text(encoding="utf-8"),
            '{"active_provider":"openai-codex"}\n',
        )
        self.assertTrue((legacy / "auth.json").exists())

    def test_completed_legacy_migration_does_not_recompare_mutable_auth(self) -> None:
        legacy = Path(self.temporary.name) / "legacy-hermes"
        legacy.mkdir(mode=0o700)
        (legacy / "auth.json").write_text('{"token":"initial"}\n')

        first = prepare_library_state(self.paths, legacy_hermes_home=legacy)
        self.assertEqual(first["legacy_hermes"], "migrated")
        (legacy / "auth.json").write_text('{"token":"rotated"}\n')

        second = prepare_library_state(self.paths, legacy_hermes_home=legacy)
        self.assertEqual(second["legacy_hermes"], "already-present")
        self.assertEqual(
            (self.paths.hermes / "auth.json").read_text(), '{"token":"initial"}\n'
        )

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
