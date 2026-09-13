"""Versioned, local-only IPC transport for the JL runtime boundary."""

from __future__ import annotations

import json
import os
import socket
import stat
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

PROTOCOL_VERSION = 1
DEFAULT_MAX_REQUEST_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 2.0


class IPCError(RuntimeError):
    """Base class for local IPC failures."""


class IPCStartupError(IPCError):
    """Raised when the local endpoint cannot be created safely."""


class IPCProtocolError(IPCError):
    """Raised when a peer sends an invalid protocol frame."""

    def __init__(self, code: str, message: str, *, request_id: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class IPCRequestEnvelope:
    protocol_version: int
    request_id: str
    caller_id: str
    session_id: str
    operation: str
    payload: Mapping[str, Any]
    credential: str | None = None

    @classmethod
    def from_mapping(cls, value: object) -> IPCRequestEnvelope:
        if not isinstance(value, dict):
            raise IPCProtocolError("malformed_request", "request must be an object")
        data = cast(dict[str, Any], value)
        expected = {
            "protocol_version",
            "request_id",
            "caller_id",
            "session_id",
            "operation",
            "payload",
            "credential",
        }
        if set(data) != expected:
            raise IPCProtocolError(
                "malformed_request", "request fields do not match the protocol schema"
            )

        request_id = _bounded_text(data.get("request_id"), "request_id", 128)
        version = data.get("protocol_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise IPCProtocolError(
                "malformed_request",
                "protocol_version must be an integer",
                request_id=request_id,
            )
        if version != PROTOCOL_VERSION:
            raise IPCProtocolError(
                "unsupported_protocol",
                f"protocol version {version} is not supported",
                request_id=request_id,
            )
        payload = data.get("payload")
        if not isinstance(payload, dict):
            raise IPCProtocolError(
                "malformed_request", "payload must be an object", request_id=request_id
            )
        return cls(
            protocol_version=version,
            request_id=request_id,
            caller_id=_bounded_text(data.get("caller_id"), "caller_id", 256),
            session_id=_bounded_text(data.get("session_id"), "session_id", 256),
            operation=_bounded_text(data.get("operation"), "operation", 128),
            payload=MappingProxyType(dict(payload)),
            credential=_credential_value(data.get("credential"), request_id),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "caller_id": self.caller_id,
            "session_id": self.session_id,
            "operation": self.operation,
            "payload": dict(self.payload),
            "credential": self.credential,
        }


@dataclass(frozen=True, slots=True)
class IPCResponseEnvelope:
    protocol_version: int
    request_id: str
    ok: bool
    result: Mapping[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def success(
        cls, request_id: str, result: Mapping[str, Any]
    ) -> IPCResponseEnvelope:
        return cls(PROTOCOL_VERSION, request_id, True, MappingProxyType(dict(result)))

    @classmethod
    def failure(
        cls, request_id: str, code: str, message: str
    ) -> IPCResponseEnvelope:
        return cls(
            PROTOCOL_VERSION,
            request_id,
            False,
            error_code=code,
            error_message=message,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "ok": self.ok,
            "result": dict(self.result) if self.result is not None else None,
            "error": (
                None
                if self.error_code is None
                else {"code": self.error_code, "message": self.error_message}
            ),
        }


RequestHandler = Callable[[IPCRequestEnvelope], IPCResponseEnvelope]


class UnixSocketServer:
    """Small AF_UNIX server that performs framing and schema validation only."""

    def __init__(
        self,
        endpoint: Path,
        handler: RequestHandler,
        *,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if max_request_bytes < 1 or timeout_seconds <= 0:
            raise ValueError("IPC bounds must be positive")
        self.endpoint = endpoint
        self.handler = handler
        self.max_request_bytes = max_request_bytes
        self.timeout_seconds = timeout_seconds
        self._socket: socket.socket | None = None
        self._socket_identity: tuple[int, int] | None = None
        self._stop = threading.Event()

    @property
    def family(self) -> socket.AddressFamily:
        return socket.AF_UNIX

    def start(self) -> None:
        if self._socket is not None:
            raise IPCStartupError("IPC server is already started")
        _prepare_runtime_directory(self.endpoint.parent)
        _recover_stale_endpoint(self.endpoint, self.timeout_seconds)

        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        bound_identity: tuple[int, int] | None = None
        try:
            listener.bind(str(self.endpoint))
            endpoint_stat = self.endpoint.lstat()
            bound_identity = (endpoint_stat.st_dev, endpoint_stat.st_ino)
            os.chmod(self.endpoint, 0o600)
            listener.listen(8)
            listener.settimeout(min(self.timeout_seconds, 0.2))
        except Exception:
            listener.close()
            if bound_identity is not None:
                _unlink_owned_socket(self.endpoint, bound_identity)
            raise
        self._socket = listener
        self._socket_identity = bound_identity
        self._stop.clear()

    def serve_forever(self) -> None:
        listener = self._socket
        if listener is None:
            raise IPCStartupError("IPC server has not been started")
        while not self._stop.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                raise
            with connection:
                connection.settimeout(self.timeout_seconds)
                response = self._handle_connection(connection)
                try:
                    connection.sendall(_encode_frame(response.to_mapping()))
                except (BrokenPipeError, ConnectionError, TimeoutError):
                    continue

    def shutdown(self) -> None:
        self._stop.set()
        listener = self._socket
        self._socket = None
        if listener is not None:
            listener.close()
        _unlink_owned_socket(self.endpoint, self._socket_identity)
        self._socket_identity = None

    def _handle_connection(self, connection: socket.socket) -> IPCResponseEnvelope:
        try:
            raw = _read_frame(connection, self.max_request_bytes)
            decoded = json.loads(raw)
            request = IPCRequestEnvelope.from_mapping(decoded)
            response = self.handler(request)
            if response.request_id != request.request_id:
                raise IPCProtocolError(
                    "invalid_response", "handler response request_id does not match"
                )
            return response
        except IPCProtocolError as error:
            return IPCResponseEnvelope.failure(
                error.request_id, error.code, str(error)
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            return IPCResponseEnvelope.failure(
                "", "malformed_request", "request must be valid UTF-8 JSON"
            )
        except TimeoutError:
            return IPCResponseEnvelope.failure(
                "", "request_timeout", "request timed out"
            )
        except Exception:
            return IPCResponseEnvelope.failure(
                "", "internal_error", "request processing failed"
            )

    def __enter__(self) -> UnixSocketServer:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()


def send_request(
    endpoint: Path,
    request: IPCRequestEnvelope,
    *,
    max_response_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Send one request to a local JL socket and return its decoded envelope."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout_seconds)
        client.connect(str(endpoint))
        client.sendall(_encode_frame(request.to_mapping()))
        raw = _read_frame(client, max_response_bytes)
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise IPCProtocolError("malformed_response", "response must be an object")
    return decoded


def _encode_frame(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise IPCProtocolError(
            "malformed_message", "message is not JSON-safe"
        ) from error
    return encoded + b"\n"


def _read_frame(connection: socket.socket, maximum: int) -> str:
    data = bytearray()
    while len(data) <= maximum:
        chunk = connection.recv(min(4096, maximum + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        newline = data.find(b"\n")
        if newline >= 0:
            if data[newline + 1 :]:
                raise IPCProtocolError(
                    "malformed_request", "only one request frame is allowed"
                )
            del data[newline:]
            break
    if len(data) > maximum:
        raise IPCProtocolError("request_too_large", "request exceeds size limit")
    if not data:
        raise IPCProtocolError("malformed_request", "request frame is empty")
    return data.decode("utf-8")


def _bounded_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise IPCProtocolError(
            "malformed_request", f"{field} must be non-empty bounded text"
        )
    return value


def _credential_value(value: object, request_id: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 512:
        raise IPCProtocolError(
            "malformed_credential",
            "credential has an invalid format",
            request_id=request_id,
        )
    return value


def _prepare_runtime_directory(directory: Path) -> None:
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    details = directory.stat()
    if not stat.S_ISDIR(details.st_mode):
        raise IPCStartupError("IPC runtime path is not a directory")
    if details.st_uid != os.geteuid():
        raise IPCStartupError("IPC runtime directory is not owned by this user")
    if stat.S_IMODE(details.st_mode) & 0o077:
        raise IPCStartupError("IPC runtime directory permissions must be user-only")


def _recover_stale_endpoint(endpoint: Path, timeout_seconds: float) -> None:
    try:
        details = endpoint.lstat()
    except FileNotFoundError:
        return
    if details.st_uid != os.geteuid() or not stat.S_ISSOCK(details.st_mode):
        raise IPCStartupError("existing IPC endpoint is not an owned Unix socket")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(min(timeout_seconds, 0.2))
        try:
            probe.connect(str(endpoint))
        except (ConnectionRefusedError, FileNotFoundError):
            endpoint.unlink()
            return
        except TimeoutError as error:
            raise IPCStartupError("existing IPC endpoint did not respond") from error
        except OSError as error:
            raise IPCStartupError(
                "existing IPC endpoint could not be verified"
            ) from error
    raise IPCStartupError("an IPC server is already active at the endpoint")


def _unlink_owned_socket(
    endpoint: Path, expected_identity: tuple[int, int] | None
) -> None:
    try:
        details = endpoint.lstat()
    except FileNotFoundError:
        return
    identity = (details.st_dev, details.st_ino)
    if (
        details.st_uid == os.geteuid()
        and stat.S_ISSOCK(details.st_mode)
        and (expected_identity is None or expected_identity == identity)
    ):
        endpoint.unlink()
