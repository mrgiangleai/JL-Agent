from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from jl_agent.control.auth import (
    AuthenticatedRequestHandler,
    FileCredentialProvider,
)
from jl_agent.control.ipc import (
    PROTOCOL_VERSION,
    IPCProtocolError,
    IPCRequestEnvelope,
    IPCResponseEnvelope,
)


class IPCAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temporary.name) / "runtime"
        self.store = FileCredentialProvider(self.runtime / "ipc.credential")
        self.calls = 0

        def privileged(request: IPCRequestEnvelope) -> IPCResponseEnvelope:
            self.calls += 1
            return IPCResponseEnvelope.success(request.request_id, {"accepted": True})

        self.handler = AuthenticatedRequestHandler(self.store, privileged)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, credential: str | None) -> IPCRequestEnvelope:
        return IPCRequestEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id="request-1",
            caller_id="native-app",
            session_id="session-1",
            operation="prepare",
            payload={},
            credential=credential,
        )

    def test_valid_credential_reaches_privileged_handler(self) -> None:
        credential = self.store.load_or_create()

        response = self.handler(self.request(credential))

        self.assertTrue(response.ok)
        self.assertEqual(self.calls, 1)
        self.assertEqual(stat.S_IMODE(self.runtime.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        self.assertEqual(self.store.path.stat().st_uid, os.geteuid())

    def test_missing_invalid_and_malformed_credentials_fail_closed(self) -> None:
        credential = self.store.load_or_create()
        missing = self.handler(self.request(None))
        invalid = self.handler(self.request(credential + "changed"))

        malformed_mapping = self.request(None).to_mapping()
        malformed_mapping["credential"] = {"secret": credential}

        with self.assertRaisesRegex(
            IPCProtocolError, "credential has an invalid format"
        ):
            IPCRequestEnvelope.from_mapping(malformed_mapping)
        self.assertFalse(missing.ok)
        self.assertFalse(invalid.ok)
        self.assertEqual(missing.error_code, "authentication_failed")
        self.assertEqual(invalid.error_code, "authentication_failed")
        self.assertEqual(self.calls, 0)

    def test_rotation_and_missing_file_recreation(self) -> None:
        original = self.store.load_or_create()
        rotated = self.store.rotate()

        self.assertNotEqual(original, rotated)
        self.assertFalse(self.store.authenticate(original))
        self.assertTrue(self.store.authenticate(rotated))

        self.store.path.unlink()
        recreated = self.store.load_or_create()
        self.assertNotEqual(rotated, recreated)
        self.assertTrue(self.store.authenticate(recreated))

    def test_unsafe_permissions_fail_closed_without_exposing_secret(self) -> None:
        credential = self.store.load_or_create()
        os.chmod(self.store.path, 0o644)

        response = self.handler(self.request(credential))

        self.assertFalse(response.ok)
        self.assertEqual(response.error_code, "authentication_failed")
        self.assertNotIn(credential, response.error_message or "")
        self.assertEqual(self.calls, 0)


if __name__ == "__main__":
    unittest.main()
