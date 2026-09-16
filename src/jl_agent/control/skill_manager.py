"""Authenticated JL Skill Manager control surface.

The manager is deliberately not a skill registry or loader.  It validates the
user-selected import boundary, then delegates skill format, quarantine, scan,
installation, metadata, and lazy loading to the pinned Hermes checkout.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .audit import AuditLedger
from .ipc import IPCRequestEnvelope, IPCResponseEnvelope


MAX_SOURCE_PATH = 4096
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_PREVIEW_CHARS = 8_192
MAX_SCAN_FINDINGS = 32
MAX_LIST_LIMIT = 100
MAX_LIST_OFFSET = 1_000_000


class SkillManagerError(ValueError):
    """Safe, user-facing failure from the manager boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


@dataclass(frozen=True, slots=True)
class ScanReport:
    verdict: str
    trust_level: str
    summary: str
    findings: tuple[Mapping[str, object], ...]
    content_hash: str
    provenance: Mapping[str, object]
    allowed: bool = False
    policy_reason: str = ""
    raw_result: object | None = None


class HermesSkillsGateway(Protocol):
    """The small Hermes surface needed by :class:`SkillManager`.

    The protocol is intentionally expressed in lifecycle operations rather than
    exposing a second JL registry.  The real implementation below maps these
    calls to Hermes modules; tests use a deterministic fake.
    """

    def validate_skill_name(self, name: str) -> str: ...

    def validate_bundle_path(self, path: str) -> str: ...

    def parse_frontmatter(self, content: str) -> Mapping[str, object]: ...

    def file_limits(self) -> tuple[int, int, int]: ...

    def quarantine(
        self,
        *,
        name: str,
        files: Mapping[str, bytes],
        metadata: Mapping[str, object],
    ) -> Path: ...

    def scan_quarantine(self, path: Path) -> ScanReport: ...

    def install(
        self,
        *,
        quarantine_path: Path,
        name: str,
        files: Mapping[str, bytes],
        metadata: Mapping[str, object],
        scan: ScanReport,
    ) -> Mapping[str, object]: ...

    def is_disabled(self, name: str) -> bool: ...

    def persist_disabled(self, name: str) -> Mapping[str, object]: ...

    def set_enabled(self, name: str, enabled: bool) -> Mapping[str, object]: ...

    def list_managed(self, *, limit: int, offset: int) -> Mapping[str, object]: ...

    def preview(self, name: str, max_chars: int) -> Mapping[str, object]: ...

    def scan_managed(self, name: str) -> Mapping[str, object]: ...


class SkillManager:
    """Handle authenticated, UI-only skill lifecycle requests."""

    OPERATIONS = frozenset(
        {
            "skills-list",
            "skill-preview",
            "skill-import",
            "skill-scan",
            "skill-enable",
            "skill-disable",
        }
    )

    def __init__(self, gateway: HermesSkillsGateway, audit: AuditLedger) -> None:
        self.gateway = gateway
        self.audit = audit

    def __call__(self, envelope: IPCRequestEnvelope) -> IPCResponseEnvelope:
        if envelope.operation not in self.OPERATIONS:
            return IPCResponseEnvelope.failure(
                envelope.request_id, "unsupported_operation", "unsupported skill operation"
            )
        try:
            result = self._dispatch(envelope)
        except SkillManagerError as error:
            self._audit(
                envelope,
                f"{self._event_name(envelope.operation)}_denied",
                policy_decision="denied",
                error_category=error.code,
            )
            return IPCResponseEnvelope.failure(
                envelope.request_id, error.code, error.public_message
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self._audit(
                envelope,
                f"{self._event_name(envelope.operation)}_failed",
                policy_decision="failed",
                error_category="manager_error",
            )
            return IPCResponseEnvelope.failure(
                envelope.request_id, "skill_manager_error", "skill manager operation failed"
            )
        self._audit(
            envelope,
            self._event_name(envelope.operation),
            policy_decision=(
                "visibility_only"
                if envelope.operation in {"skill-enable", "skill-disable"}
                else "completed"
            ),
            skill=result,
        )
        return IPCResponseEnvelope.success(envelope.request_id, result)

    def _dispatch(self, envelope: IPCRequestEnvelope) -> Mapping[str, object]:
        payload = _strict_mapping(envelope.payload, envelope.operation)
        operation = envelope.operation
        if operation == "skills-list":
            limit = _bounded_int(payload, "limit", default=MAX_LIST_LIMIT, minimum=1, maximum=MAX_LIST_LIMIT)
            offset = _bounded_int(payload, "offset", default=0, minimum=0, maximum=MAX_LIST_OFFSET)
            return _bounded_result(self.gateway.list_managed(limit=limit, offset=offset))
        if operation == "skill-preview":
            name = _name(payload)
            max_chars = _bounded_int(
                payload, "max_chars", default=MAX_PREVIEW_CHARS, minimum=1, maximum=MAX_PREVIEW_CHARS
            )
            return self._bounded_preview(self.gateway.preview(name, max_chars), max_chars)
        if operation == "skill-import":
            return self._import_local(_source_path(payload))
        if operation == "skill-scan":
            return _bounded_result(self.gateway.scan_managed(_name(payload)))
        if operation in {"skill-enable", "skill-disable"}:
            name = _name(payload)
            return _bounded_result(
                self.gateway.set_enabled(name, enabled=operation == "skill-enable")
            )
        raise SkillManagerError("unsupported_operation", "unsupported skill operation")

    def _import_local(self, source: Path) -> Mapping[str, object]:
        files = self._collect_files(source)
        skill_md = files.get("SKILL.md")
        if skill_md is None:
            raise SkillManagerError("invalid_skill", "import requires a root SKILL.md")
        try:
            frontmatter = dict(
                self.gateway.parse_frontmatter(skill_md.decode("utf-8-sig", errors="replace"))
            )
        except (TypeError, ValueError):
            raise SkillManagerError("invalid_skill", "SKILL.md frontmatter is invalid") from None
        raw_name = frontmatter.get("name")
        raw_description = frontmatter.get("description")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise SkillManagerError("invalid_skill", "SKILL.md requires a non-empty name")
        if not isinstance(raw_description, str) or not raw_description.strip():
            raise SkillManagerError("invalid_skill", "SKILL.md requires a non-empty description")
        if len(raw_name.strip()) > MAX_NAME:
            raise SkillManagerError("invalid_skill", "skill name exceeds the Hermes limit")
        if len(raw_description.strip()) > MAX_DESCRIPTION:
            raise SkillManagerError("invalid_skill", "skill description exceeds the Hermes limit")
        try:
            name = self.gateway.validate_skill_name(raw_name.strip())
        except (TypeError, ValueError):
            raise SkillManagerError("invalid_skill", "skill name is not a safe Hermes name") from None

        metadata = {
            "source": "local-folder",
            "source_kind": "local-folder",
            "license": _bounded_text(frontmatter.get("license"), 256),
            "imported": True,
        }
        quarantine_path = self.gateway.quarantine(
            name=name, files=files, metadata=metadata
        )
        scan = self.gateway.scan_quarantine(quarantine_path)
        if not scan.allowed:
            raise SkillManagerError(
                "skill_scan_blocked",
                f"Hermes Skills Guard blocked import ({scan.verdict or 'unknown'})",
            )
        if not self.gateway.is_disabled(name):
            # Persist Disabled before the bundle enters active Hermes storage.
            self.gateway.persist_disabled(name)
        if not self.gateway.is_disabled(name):
            raise SkillManagerError("disabled_state_failed", "skill remained enabled after import")
        installed = dict(
            self.gateway.install(
                quarantine_path=quarantine_path,
                name=name,
                files=files,
                metadata=metadata,
                scan=scan,
            )
        )
        installed["enabled"] = False
        installed["source"] = "local-folder"
        installed["scan_verdict"] = scan.verdict
        installed["content_hash"] = scan.content_hash
        installed["provenance"] = dict(scan.provenance)
        return _bounded_result(installed)

    def _collect_files(self, source: Path) -> dict[str, bytes]:
        if (
            not source.is_absolute()
            or ".." in source.parts
            or len(str(source)) > MAX_SOURCE_PATH
        ):
            raise SkillManagerError("invalid_source", "source path must be an absolute bounded path")
        try:
            source_stat = source.lstat()
        except FileNotFoundError:
            raise SkillManagerError("invalid_source", "source path does not exist") from None
        if source.is_symlink() or not (source_stat.st_mode & 0o170000) in {0o040000, 0o100000}:
            raise SkillManagerError("invalid_source", "source must be a regular folder or SKILL.md")

        if source.is_file():
            if source.name != "SKILL.md":
                raise SkillManagerError("invalid_source", "file import must select SKILL.md")
            candidates = [(source, Path("SKILL.md"))]
        else:
            candidates = []
            for root, directories, filenames in os.walk(source, topdown=True, followlinks=False):
                root_path = Path(root)
                for directory in list(directories):
                    child = root_path / directory
                    if child.is_symlink():
                        raise SkillManagerError("unsafe_source", "symlinks are not allowed in a skill")
                for filename in filenames:
                    child = root_path / filename
                    if child.is_symlink():
                        raise SkillManagerError("unsafe_source", "symlinks are not allowed in a skill")
                    try:
                        details = child.lstat()
                    except OSError:
                        raise SkillManagerError("invalid_source", "skill file could not be inspected") from None
                    if not (details.st_mode & 0o170000) == 0o100000:
                        raise SkillManagerError("invalid_source", "skill contains a non-regular file")
                    candidates.append((child, child.relative_to(source)))
            if not any(relative.as_posix() == "SKILL.md" for _, relative in candidates):
                raise SkillManagerError("invalid_skill", "import requires a root SKILL.md")

        max_files, max_total_kb, max_single_kb = self.gateway.file_limits()
        if len(candidates) > max_files:
            raise SkillManagerError("invalid_skill", "skill contains too many files")
        total = 0
        files: dict[str, bytes] = {}
        for path, relative in candidates:
            try:
                safe_relative = self.gateway.validate_bundle_path(relative.as_posix())
                content = path.read_bytes()
            except (OSError, TypeError, ValueError):
                raise SkillManagerError("unsafe_source", "skill contains an unsafe file path") from None
            if len(content) > max_single_kb * 1024:
                raise SkillManagerError("invalid_skill", "skill contains an oversized file")
            total += len(content)
            if total > max_total_kb * 1024:
                raise SkillManagerError("invalid_skill", "skill bundle exceeds the size limit")
            files[safe_relative] = content
        return files

    @staticmethod
    def _bounded_preview(result: Mapping[str, object], max_chars: int) -> Mapping[str, object]:
        bounded = dict(result)
        content = bounded.get("content")
        if isinstance(content, str):
            bounded["content"] = content[:max_chars]
            bounded["truncated"] = len(content) > max_chars
        else:
            bounded.pop("content", None)
            bounded["truncated"] = False
        bounded.pop("linked_files", None)
        return _bounded_result(bounded)

    @staticmethod
    def _event_name(operation: str) -> str:
        return {
            "skills-list": "skill_listed",
            "skill-preview": "skill_previewed",
            "skill-import": "skill_imported",
            "skill-scan": "skill_scanned",
            "skill-enable": "skill_enabled",
            "skill-disable": "skill_disabled",
        }[operation]

    def _audit(
        self,
        envelope: IPCRequestEnvelope,
        event: str,
        *,
        policy_decision: str,
        skill: Mapping[str, object] | None = None,
        error_category: str = "",
    ) -> None:
        item = skill or {}
        content_hash = item.get("content_hash")
        provenance = item.get("provenance") or item.get("source")
        if isinstance(provenance, Mapping):
            provenance = (
                "local-folder"
                if provenance.get("source_url") == "local-folder"
                else provenance.get("source") or provenance.get("source_kind")
            )
        self.audit.record(
            event,
            request_id=envelope.request_id,
            caller_id=envelope.caller_id,
            session_id=envelope.session_id,
            capability_id="core.jl.skill-manager",
            action_class=("skill.metadata",),
            policy_decision=policy_decision,
            fingerprint_reference=_bounded_text(content_hash, 128),
            provider=_bounded_text(provenance, 128),
            error_category=error_category,
        )


class PinnedHermesSkillsGateway:
    """Adapter to the pinned Hermes skill primitives, loaded lazily."""

    def __init__(self, hermes_root: str | Path, hermes_home: str | Path) -> None:
        self.hermes_root = Path(hermes_root).absolute()
        self.hermes_home = Path(hermes_home).absolute()
        self._modules: dict[str, Any] | None = None
        self._lock = threading.RLock()

    def _load_modules(self) -> dict[str, Any]:
        if self._modules is None:
            root = str(self.hermes_root)
            if root not in sys.path:
                sys.path.insert(0, root)
            try:
                import hermes_constants
                from hermes_cli import config as hermes_config
                from hermes_cli import skills_config
                from tools import skills_guard, skills_hub, skills_hub_install, skills_hub_models, skills_tool
            except (ImportError, OSError) as error:
                raise SkillManagerError("hermes_unavailable", "Hermes skill services are unavailable") from error
            self._modules = {
                "constants": hermes_constants,
                "config": hermes_config,
                "skills_config": skills_config,
                "guard": skills_guard,
                "hub": skills_hub,
                "hub_install": skills_hub_install,
                "models": skills_hub_models,
                "tool": skills_tool,
            }
        return self._modules

    @contextmanager
    def _home(self):
        modules = self._load_modules()
        token = modules["constants"].set_hermes_home_override(self.hermes_home)
        try:
            yield modules
        finally:
            modules["constants"].reset_hermes_home_override(token)

    def validate_skill_name(self, name: str) -> str:
        with self._lock, self._home() as modules:
            return modules["models"]._validate_skill_name(name)

    def validate_bundle_path(self, path: str) -> str:
        with self._lock, self._home() as modules:
            return modules["models"]._validate_bundle_rel_path(path)

    def parse_frontmatter(self, content: str) -> Mapping[str, object]:
        with self._lock, self._home() as modules:
            parsed = modules["models"]._parse_frontmatter(content)
            return parsed if isinstance(parsed, dict) else {}

    def file_limits(self) -> tuple[int, int, int]:
        with self._lock, self._home() as modules:
            return (
                int(modules["guard"].MAX_FILE_COUNT),
                int(modules["guard"].MAX_TOTAL_SIZE_KB),
                int(modules["guard"].MAX_SINGLE_FILE_KB),
            )

    def quarantine(self, *, name: str, files: Mapping[str, bytes], metadata: Mapping[str, object]) -> Path:
        with self._lock, self._home() as modules:
            bundle = modules["models"].SkillBundle(
                name=name,
                files=dict(files),
                source="local-folder",
                identifier=f"local-folder:{name}",
                trust_level="community",
                metadata=dict(metadata),
            )
            return modules["hub_install"].quarantine_bundle(bundle)

    def scan_quarantine(self, path: Path) -> ScanReport:
        with self._lock, self._home() as modules:
            result, provenance = modules["guard"].scan_skill_cached(
                path,
                source="community",
                source_url="local-folder",
                cache_dir=modules["hub"].HUB_DIR / "scan-cache",
            )
            allowed, reason = modules["guard"].should_allow_install(result, force=False)
            findings = tuple(_finding_dict(item) for item in result.findings[:MAX_SCAN_FINDINGS])
            return ScanReport(
                verdict=str(result.verdict),
                trust_level=str(result.trust_level),
                summary=_bounded_text(result.summary, 512),
                findings=findings,
                content_hash=_bounded_text(provenance.get("bundle_hash"), 128),
                provenance=_bounded_mapping(provenance),
                allowed=bool(allowed is True),
                policy_reason=_bounded_text(reason, 512),
                raw_result=result,
            )

    def install(
        self,
        *,
        quarantine_path: Path,
        name: str,
        files: Mapping[str, bytes],
        metadata: Mapping[str, object],
        scan: ScanReport,
    ) -> Mapping[str, object]:
        with self._lock, self._home() as modules:
            target = modules["hub_install"]._resolve_lock_install_path(
                f"imported/{name}", name
            )
            if target.exists():
                raise SkillManagerError("skill_exists", "a managed skill with this name already exists")
            bundle = modules["models"].SkillBundle(
                name=name,
                files=dict(files),
                source="local-folder",
                identifier=f"local-folder:{name}",
                trust_level="community",
                metadata=dict(metadata),
            )
            raw_result = scan.raw_result
            if raw_result is None:
                raise SkillManagerError("skill_scan_missing", "Hermes scan result is unavailable")
            installed = modules["hub_install"].install_from_quarantine(
                quarantine_path,
                name,
                "imported",
                bundle,
                raw_result,
                scan_provenance=dict(scan.provenance),
            )
            return {
                "name": name,
                "category": "imported",
                "install_path": str(installed.resolve().relative_to(modules["hub"]._skills_dir().resolve())),
                "source": "local-folder",
            }

    def is_disabled(self, name: str) -> bool:
        with self._lock, self._home() as modules:
            config = modules["config"].load_config()
            return name in modules["skills_config"].get_disabled_skills(config)

    def persist_disabled(self, name: str) -> Mapping[str, object]:
        with self._lock, self._home() as modules:
            config = modules["config"].load_config()
            disabled = set(modules["skills_config"].get_disabled_skills(config))
            disabled.add(name)
            modules["skills_config"].save_disabled_skills(config, disabled)
            return {"name": name, "enabled": False}

    def set_enabled(self, name: str, enabled: bool) -> Mapping[str, object]:
        with self._lock, self._home() as modules:
            if not modules["hub"].HubLockFile().get_installed(name):
                raise SkillManagerError("skill_not_managed", "skill is not managed by JL")
            self._set_enabled_in_modules(modules, name, enabled)
            return {"name": name, "enabled": enabled}

    @staticmethod
    def _set_enabled_in_modules(modules: Mapping[str, Any], name: str, enabled: bool) -> None:
        config = modules["config"].load_config()
        disabled = set(modules["skills_config"].get_disabled_skills(config))
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        modules["skills_config"].save_disabled_skills(config, disabled)

    def list_managed(self, *, limit: int, offset: int) -> Mapping[str, object]:
        with self._lock, self._home() as modules:
            config = modules["config"].load_config()
            disabled = set(modules["skills_config"].get_disabled_skills(config))
            skills = modules["tool"]._find_all_skills(skip_disabled=True)
            lock_rows = {
                str(item.get("name")): item
                for item in modules["hub"].HubLockFile().list_installed()
                if isinstance(item, dict) and item.get("name")
            }
            metadata_by_name = {
                str(item.get("name")): item
                for item in skills
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            projected: list[dict[str, object]] = []
            # The manager lists Hermes-managed lock entries only.  Project,
            # external, and hand-made skills remain Hermes-visible but are not
            # silently advertised as JL-managed lifecycle objects.
            for name, lock in sorted(lock_rows.items()):
                item = metadata_by_name.get(name, {})
                provenance = lock.get("source") or "hermes-managed"
                projected.append(
                    {
                        "name": name,
                        "description": _bounded_text(item.get("description"), MAX_DESCRIPTION),
                        "category": _bounded_text(item.get("category"), 128),
                        "enabled": name not in disabled,
                        "provenance": _bounded_text(provenance, 128),
                        "scan_verdict": _bounded_text(lock.get("scan_verdict"), 32),
                        "content_hash": _bounded_text(lock.get("content_hash"), 128),
                        "install_path": _bounded_text(lock.get("install_path"), 512),
                    }
                )
            return {
                "skills": projected[offset : offset + limit],
                "count": len(projected),
                "offset": offset,
                "limit": limit,
            }

    def preview(self, name: str, max_chars: int) -> Mapping[str, object]:
        with self._lock, self._home() as modules:
            lock = modules["hub"].HubLockFile().get_installed(name)
            if not isinstance(lock, dict):
                raise SkillManagerError("skill_not_managed", "skill is not managed by JL")
            result = json.loads(modules["tool"].skill_view(name, preprocess=False))
            if not isinstance(result, dict) or not result.get("success"):
                # Hermes intentionally refuses skill_view for disabled skills.  A
                # manager preview is still a bounded read-only inspection, so
                # read only SKILL.md from the Hermes-validated managed path.
                install_path = lock.get("install_path")
                if not isinstance(install_path, str):
                    raise SkillManagerError("skill_path_invalid", "managed skill path is invalid")
                skill_dir = modules["hub_install"]._resolve_lock_install_path(install_path, name)
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.is_file() or skill_md.is_symlink():
                    raise SkillManagerError("skill_not_found", "managed skill could not be previewed")
                with skill_md.open("r", encoding="utf-8-sig", errors="replace") as stream:
                    content = stream.read(max_chars + 1)
                frontmatter = modules["models"]._parse_frontmatter(content)
                result = {
                    "success": True,
                    "name": frontmatter.get("name", name) if isinstance(frontmatter, dict) else name,
                    "description": frontmatter.get("description", "") if isinstance(frontmatter, dict) else "",
                    "content": content,
                }
            return {
                "name": _bounded_text(result.get("name"), MAX_NAME),
                "description": _bounded_text(result.get("description"), MAX_DESCRIPTION),
                "content": _bounded_text(result.get("content"), max_chars),
                "enabled": not self.is_disabled(name),
                "provenance": _bounded_text(lock.get("source"), 256),
            }

    def scan_managed(self, name: str) -> Mapping[str, object]:
        with self._lock, self._home() as modules:
            lock = modules["hub"].HubLockFile().get_installed(name)
            if not isinstance(lock, dict):
                raise SkillManagerError("skill_not_managed", "skill is not managed by JL")
            install_path = lock.get("install_path")
            if not isinstance(install_path, str):
                raise SkillManagerError("skill_path_invalid", "managed skill path is invalid")
            path = modules["hub_install"]._resolve_lock_install_path(install_path, name)
            report = self.scan_quarantine(path)
            return {
                "name": name,
                "verdict": report.verdict,
                "trust_level": report.trust_level,
                "summary": report.summary,
                "findings": [dict(item) for item in report.findings],
                "content_hash": report.content_hash,
                "provenance": dict(report.provenance),
                "allowed": report.allowed,
                "policy_reason": report.policy_reason,
            }


def _strict_mapping(payload: Mapping[str, Any], operation: str) -> dict[str, Any]:
    allowed = {
        "skills-list": {"limit", "offset"},
        "skill-preview": {"name", "max_chars"},
        "skill-import": {"source_path"},
        "skill-scan": {"name"},
        "skill-enable": {"name"},
        "skill-disable": {"name"},
    }[operation]
    unknown = set(payload).difference(allowed)
    if unknown:
        raise SkillManagerError("malformed_payload", f"unknown skill payload field: {sorted(unknown)[0]}")
    return dict(payload)


def _name(payload: Mapping[str, Any]) -> str:
    value = payload.get("name")
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_NAME:
        raise SkillManagerError("malformed_payload", "skill name is invalid")
    return value.strip()


def _source_path(payload: Mapping[str, Any]) -> Path:
    value = payload.get("source_path")
    if not isinstance(value, str) or not value or len(value) > MAX_SOURCE_PATH:
        raise SkillManagerError("malformed_payload", "source_path is invalid")
    return Path(value)


def _bounded_int(
    payload: Mapping[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise SkillManagerError("malformed_payload", f"{key} is outside its bound")
    return value


def _bounded_text(value: object, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    return value[:maximum]


def _bounded_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    bounded: dict[str, object] = {}
    for key, item in list(value.items())[:32]:
        if not isinstance(key, str):
            continue
        safe_key = _bounded_text(key, 128)
        if isinstance(item, str):
            bounded[safe_key] = _bounded_text(item, 512)
        elif isinstance(item, (bool, int, float)):
            bounded[safe_key] = item
        elif isinstance(item, list) and all(isinstance(entry, (str, int, float, bool)) for entry in item):
            bounded[safe_key] = [
                _bounded_text(entry, 256) if isinstance(entry, str) else entry
                for entry in item[:32]
            ]
    return bounded


def _bounded_result(value: Mapping[str, object]) -> Mapping[str, object]:
    # Results are metadata-only by contract.  Do not accidentally return a full
    # Hermes skill body or an unbounded nested scanner response through IPC.
    result = dict(value)
    if isinstance(result.get("findings"), list):
        result["findings"] = [dict(item) for item in result["findings"][:MAX_SCAN_FINDINGS] if isinstance(item, Mapping)]
    if isinstance(result.get("provenance"), Mapping):
        result["provenance"] = dict(_bounded_mapping(result["provenance"]))
    if isinstance(result.get("content"), str):
        result["content"] = result["content"][:MAX_PREVIEW_CHARS]
    result.pop("raw_result", None)
    return result


def _finding_dict(value: object) -> Mapping[str, object]:
    if hasattr(value, "__dataclass_fields__"):
        data = {
            field: getattr(value, field)
            for field in value.__dataclass_fields__
        }
    elif isinstance(value, Mapping):
        data = dict(value)
    else:
        data = {"detail": str(value)}
    return {
        _bounded_text(key, 64): _bounded_text(item, 256) if isinstance(item, str) else item
        for key, item in list(data.items())[:16]
    }
