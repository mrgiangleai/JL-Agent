#!/usr/bin/env python3
"""Test-only observability helpers for the Phase 7 smoke harness.

The live harness must call ``startup_failure_result`` before removing its
temporary home. This module never starts JL Agent or performs inference.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from jl_agent.automation_service import require_internal_apfs

MAX_RETAINED_OUTPUT_CHARS = 64 * 1024
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_SECRET_VALUE_RE = re.compile(
    r"(?i)([\"']?(?:credential|token|secret|password|api[-_ ]?key|authorization)"
    r"[\"']?\s*[:=]\s*)([\"']?[^\s,;}\]]+)",
)


class ChildProcess(Protocol):
    returncode: int | None

    def communicate(self) -> tuple[str | bytes | None, str | bytes | None]: ...


@dataclass(frozen=True, slots=True)
class ChildRuntimeOutput:
    """Sanitized output retained from one child process."""

    returncode: int | None
    stdout: str
    stderr: str


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def sanitize_runtime_output(value: str | bytes | None) -> str:
    """Redact common secret forms while retaining bounded diagnostic text."""

    text = _BEARER_RE.sub("Bearer [REDACTED]", _text(value))
    text = _SECRET_VALUE_RE.sub(r"\1[REDACTED]", text)
    if len(text) <= MAX_RETAINED_OUTPUT_CHARS:
        return text
    half = MAX_RETAINED_OUTPUT_CHARS // 2
    return (
        text[:half]
        + "\n...[sanitized runtime output truncated]...\n"
        + text[-half:]
    )


def capture_child_runtime_output(process: ChildProcess) -> ChildRuntimeOutput:
    """Drain both pipes and retain their sanitized contents.

    The caller terminates a still-running child before calling this function.
    """

    stdout, stderr = process.communicate()
    return ChildRuntimeOutput(
        returncode=process.returncode,
        stdout=sanitize_runtime_output(stdout),
        stderr=sanitize_runtime_output(stderr),
    )


def _exact_startup_error(output: ChildRuntimeOutput) -> str:
    """Return the most specific final diagnostic line available."""

    for stream in (output.stderr, output.stdout):
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        if lines:
            return lines[-1]
    code = output.returncode if output.returncode is not None else "unknown"
    return f"runtime exited during startup ({code})"


def startup_failure_result(output: ChildRuntimeOutput) -> dict[str, object]:
    """Build startup evidence before the smoke harness cleans its home."""

    return {
        "failure_layer": "runtime-startup",
        "runtime_exit_code": output.returncode,
        "runtime_startup_error": _exact_startup_error(output),
        "runtime_stdout": output.stdout,
        "runtime_stderr": output.stderr,
    }


def prepare_smoke_home(home: Path, audit_name: str = "audit.jsonl") -> Path:
    """Create a strict smoke home after the caller's APFS preflight passes."""

    require_internal_apfs(home)
    home.mkdir(mode=0o700, parents=True, exist_ok=False)
    audit = home / audit_name
    descriptor = os.open(audit, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    os.chmod(audit, 0o600)
    verify_smoke_permissions(home, audit)
    return audit


def verify_smoke_permissions(home: Path, audit: Path) -> None:
    """Keep the harness's strict user-only directory/file checks intact."""

    home_details = home.stat()
    audit_details = audit.stat()
    if (
        not stat.S_ISDIR(home_details.st_mode)
        or home_details.st_uid != os.geteuid()
        or stat.S_IMODE(home_details.st_mode) != 0o700
    ):
        raise PermissionError("smoke home must be a private user-owned 0700 directory")
    if (
        not stat.S_ISREG(audit_details.st_mode)
        or audit_details.st_uid != os.geteuid()
        or stat.S_IMODE(audit_details.st_mode) != 0o600
    ):
        raise PermissionError("smoke audit must be a private user-owned 0600 file")
