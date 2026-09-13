"""Runtime credential authentication for JL's local IPC boundary."""

from __future__ import annotations

import hmac
import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .ipc import IPCRequestEnvelope, IPCResponseEnvelope


class CredentialError(RuntimeError):
    """Raised when a credential cannot be stored or loaded safely."""


class CredentialProvider(Protocol):
    """Storage boundary that can later be implemented with macOS Keychain."""

    def load_or_create(self) -> str: ...

    def rotate(self) -> str: ...

    def authenticate(self, supplied: str) -> bool: ...


class FileCredentialProvider:
    """Store one runtime-generated credential in a private user-owned file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load_or_create(self) -> str:
        try:
            return self._load()
        except FileNotFoundError:
            return self._create_exclusive()

    def rotate(self) -> str:
        self._validate_parent()
        credential = _new_credential()
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(credential)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return credential

    def authenticate(self, supplied: str) -> bool:
        try:
            expected = self._load()
        except (CredentialError, FileNotFoundError):
            return False
        return hmac.compare_digest(expected, supplied)

    def _create_exclusive(self) -> str:
        self._validate_parent()
        credential = _new_credential()
        try:
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return self._load()
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(credential)
            stream.flush()
            os.fsync(stream.fileno())
        return credential

    def _load(self) -> str:
        details = self.path.lstat()
        if not stat.S_ISREG(details.st_mode):
            raise CredentialError("credential storage is not a regular file")
        if details.st_uid != os.geteuid():
            raise CredentialError("credential storage is not owned by this user")
        if stat.S_IMODE(details.st_mode) != 0o600:
            raise CredentialError("credential storage permissions are not private")
        credential = self.path.read_text(encoding="utf-8")
        if not _valid_stored_credential(credential):
            raise CredentialError("credential storage is malformed")
        return credential

    def _validate_parent(self) -> None:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        details = self.path.parent.stat()
        if details.st_uid != os.geteuid() or not stat.S_ISDIR(details.st_mode):
            raise CredentialError("credential directory is not user-owned")
        if stat.S_IMODE(details.st_mode) & 0o077:
            raise CredentialError("credential directory permissions are not private")


AuthenticatedHandler = Callable[[IPCRequestEnvelope], IPCResponseEnvelope]


class AuthenticatedRequestHandler:
    """Fail closed before forwarding a request to privileged processing."""

    def __init__(
        self,
        credentials: CredentialProvider,
        handler: AuthenticatedHandler,
    ) -> None:
        self.credentials = credentials
        self.handler = handler

    def __call__(self, request: IPCRequestEnvelope) -> IPCResponseEnvelope:
        supplied = request.credential
        if supplied is None:
            return IPCResponseEnvelope.failure(
                request.request_id,
                "authentication_failed",
                "authentication is required",
            )
        if not self.credentials.authenticate(supplied):
            return IPCResponseEnvelope.failure(
                request.request_id,
                "authentication_failed",
                "authentication failed",
            )
        return self.handler(request)


def _new_credential() -> str:
    return secrets.token_urlsafe(32)


def _valid_stored_credential(value: str) -> bool:
    return 32 <= len(value) <= 512 and "\n" not in value and "\r" not in value
