from __future__ import annotations

import json
import os
import socket
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from jl_agent.control.ipc import (
    PROTOCOL_VERSION,
    IPCRequestEnvelope,
    IPCResponseEnvelope,
    IPCStartupError,
    UnixSocketServer,
    send_request,
)


class LocalIPCTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temporary.name) / "runtime"
        self.endpoint = self.runtime / "jl-agent.sock"
        self.server: UnixSocketServer | None = None
        self.thread: threading.Thread | None = None

    def tearDown(self) -> None:
        if self.server is not None:
            self.server.shutdown()
        if self.thread is not None:
            self.thread.join(timeout=1)
        self.temporary.cleanup()

    def start_server(self, *, max_request_bytes: int = 64 * 1024) -> None:
        self.server = UnixSocketServer(
            self.endpoint,
            lambda request: IPCResponseEnvelope.success(
                request.request_id, {"operation": request.operation}
            ),
            max_request_bytes=max_request_bytes,
            timeout_seconds=0.2,
        )
        self.server.start()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, **changes: object) -> IPCRequestEnvelope:
        values: dict[str, object] = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "request-1",
            "caller_id": "native-app",
            "session_id": "session-1",
            "operation": "status",
            "payload": {},
            "credential": None,
        }
        values.update(changes)
        return IPCRequestEnvelope(**values)  # type: ignore[arg-type]

    def raw_request(self, value: object) -> dict[str, object]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect(str(self.endpoint))
            client.sendall(json.dumps(value).encode() + b"\n")
            data = client.recv(4096)
        decoded = json.loads(data)
        assert isinstance(decoded, dict)
        return decoded

    def test_round_trip_uses_unix_socket_and_user_only_permissions(self) -> None:
        self.start_server()

        response = send_request(self.endpoint, self.request())

        assert self.server is not None
        self.assertEqual(self.server.family, socket.AF_UNIX)
        self.assertTrue(response["ok"])
        self.assertEqual(response["request_id"], "request-1")
        self.assertEqual(response["result"], {"operation": "status"})
        self.assertEqual(stat.S_IMODE(self.runtime.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.endpoint.stat().st_mode), 0o600)
        self.assertEqual(self.endpoint.stat().st_uid, os.geteuid())

    def test_malformed_and_unsupported_requests_are_rejected(self) -> None:
        self.start_server()

        malformed = self.raw_request({"protocol_version": PROTOCOL_VERSION})
        unsupported = self.raw_request(
            {
                **self.request().to_mapping(),
                "protocol_version": PROTOCOL_VERSION + 1,
            }
        )

        self.assertEqual(malformed["error"]["code"], "malformed_request")  # type: ignore[index]
        self.assertEqual(
            unsupported["error"]["code"], "unsupported_protocol"  # type: ignore[index]
        )

    def test_oversized_request_is_rejected(self) -> None:
        self.start_server(max_request_bytes=128)

        response = self.raw_request(
            {**self.request().to_mapping(), "payload": {"padding": "x" * 512}}
        )

        self.assertEqual(response["error"]["code"], "request_too_large")  # type: ignore[index]

    def test_incomplete_request_times_out(self) -> None:
        self.start_server()

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect(str(self.endpoint))
            client.sendall(b'{"protocol_version":')
            response = json.loads(client.recv(4096))

        self.assertEqual(response["error"]["code"], "request_timeout")

    def test_stale_owned_socket_is_recovered(self) -> None:
        self.runtime.mkdir(mode=0o700)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(self.endpoint))
        stale.close()

        self.start_server()

        response = send_request(self.endpoint, self.request())
        self.assertTrue(response["ok"])

    def test_active_endpoint_is_preserved_and_second_server_is_rejected(self) -> None:
        self.start_server()
        second = UnixSocketServer(
            self.endpoint,
            lambda request: IPCResponseEnvelope.success(request.request_id, {}),
            timeout_seconds=0.2,
        )

        with self.assertRaisesRegex(IPCStartupError, "already active"):
            second.start()

        response = send_request(self.endpoint, self.request())
        self.assertTrue(response["ok"])

    def test_regular_file_is_never_removed_as_stale_endpoint(self) -> None:
        self.runtime.mkdir(mode=0o700)
        self.endpoint.write_text("do not remove")
        server = UnixSocketServer(
            self.endpoint,
            lambda request: IPCResponseEnvelope.success(request.request_id, {}),
        )

        with self.assertRaises(IPCStartupError):
            server.start()
        self.assertEqual(self.endpoint.read_text(), "do not remove")

    def test_shutdown_removes_only_the_server_socket(self) -> None:
        self.start_server()
        assert self.server is not None

        self.server.shutdown()
        self.thread.join(timeout=1)  # type: ignore[union-attr]

        self.assertFalse(self.endpoint.exists())


if __name__ == "__main__":
    unittest.main()
