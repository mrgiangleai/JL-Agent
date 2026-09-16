"""Read-only projection of selected Hermes capabilities into JL descriptors."""

from __future__ import annotations

import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .registry import (
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilityType,
    ConfigurationRequirements,
    Entrypoint,
    EntrypointKind,
    Health,
    HealthState,
    Source,
)

HERMES_REPOSITORY = "https://github.com/NousResearch/hermes-agent.git"
HERMES_VERSION = "0.21.2"
HERMES_REVISION = "044a77b3b6af4ce16138d42762f812a20b9f7a89"


class HermesProjectionError(RuntimeError):
    """Raised when the pinned Hermes checkout cannot be safely identified."""


@dataclass(frozen=True, slots=True)
class HermesIdentity:
    repository: str
    version: str
    revision: str


@dataclass(frozen=True, slots=True)
class HermesCapabilitySpec:
    id: str
    name: str
    capability_type: CapabilityType
    entrypoint_kind: EntrypointKind
    entrypoint_address: str
    module_path: str
    evidence: str
    permissions: tuple[str, ...]
    configuration_required: tuple[str, ...] = ()
    configuration_optional: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HermesProjectionResult:
    identity: HermesIdentity
    registry: CapabilityRegistry
    availability: dict[str, bool]


CAPABILITY_SPECS = (
    HermesCapabilitySpec(
        id="core.hermes.files",
        name="Hermes File Tools",
        capability_type=CapabilityType.TOOL,
        entrypoint_kind=EntrypointKind.HERMES_TOOL,
        entrypoint_address="file",
        module_path="tools/file_tools.py",
        evidence='registry.register(name="read_file", toolset="file"',
        permissions=("local.read", "local.write.reversible", "local.delete"),
    ),
    HermesCapabilitySpec(
        id="core.hermes.terminal",
        name="Hermes Terminal Tools",
        capability_type=CapabilityType.TOOL,
        entrypoint_kind=EntrypointKind.HERMES_TOOL,
        entrypoint_address="terminal",
        module_path="tools/terminal_tool.py",
        evidence='name="terminal"',
        permissions=("local.read", "local.write.reversible", "local.delete"),
    ),
    HermesCapabilitySpec(
        id="core.hermes.browser",
        name="Hermes Browser Tools",
        capability_type=CapabilityType.TOOL,
        entrypoint_kind=EntrypointKind.HERMES_TOOL,
        entrypoint_address="browser",
        module_path="tools/browser_tool.py",
        evidence='toolset="browser"',
        permissions=("network.read", "external.send"),
        configuration_optional=("browser.backend",),
    ),
    HermesCapabilitySpec(
        id="core.hermes.memory",
        name="Hermes Memory",
        capability_type=CapabilityType.MEMORY,
        entrypoint_kind=EntrypointKind.HERMES_TOOL,
        entrypoint_address="memory",
        module_path="tools/memory_tool.py",
        evidence='name="memory"',
        permissions=("local.read", "local.write.reversible", "local.delete"),
        configuration_optional=("memory.provider",),
    ),
    HermesCapabilitySpec(
        id="core.hermes.mcp",
        name="Hermes MCP Client",
        capability_type=CapabilityType.SERVICE,
        entrypoint_kind=EntrypointKind.PLUGIN,
        entrypoint_address="tools.mcp_tool:discover_mcp_tools",
        module_path="tools/mcp_tool.py",
        evidence="discover_mcp_tools",
        permissions=("network.read", "external.send", "credential.use"),
        configuration_optional=("mcp_servers",),
    ),
    HermesCapabilitySpec(
        id="core.hermes.computer-use",
        name="Hermes macOS Computer Use",
        capability_type=CapabilityType.TOOL,
        entrypoint_kind=EntrypointKind.HERMES_TOOL,
        entrypoint_address="computer_use",
        module_path="tools/computer_use_tool.py",
        evidence='name="computer_use"',
        permissions=(
            "local.read",
            "screen.capture",
            "input.control",
            "local.delete",
            "external.send",
            "credential.use",
            "finance.transact",
        ),
        configuration_optional=(
            "computer_use.permission_mode",
            "computer_use.capability_manifest",
        ),
    ),
)


class HermesProjection:
    """Inspect a pinned checkout without importing or starting Hermes."""

    def __init__(
        self,
        root: str | Path,
        *,
        revision_reader: Callable[[Path], str] | None = None,
    ) -> None:
        self.root = Path(root)
        self._revision_reader = revision_reader or _packaged_or_git_revision

    def inspect_identity(self) -> HermesIdentity:
        pyproject_path = self.root / "pyproject.toml"
        try:
            with pyproject_path.open("rb") as stream:
                project = tomllib.load(stream)["project"]
            version = project["version"]
        except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
            raise HermesProjectionError(
                f"cannot read Hermes identity from {pyproject_path}"
            ) from error
        if not isinstance(version, str):
            raise HermesProjectionError("Hermes project.version must be a string")
        revision = self._revision_reader(self.root)
        identity = HermesIdentity(
            repository=HERMES_REPOSITORY,
            version=version,
            revision=revision,
        )
        if identity.version != HERMES_VERSION or identity.revision != HERMES_REVISION:
            raise HermesProjectionError(
                "Hermes checkout does not match the JL pinned version and revision"
            )
        return identity

    def inspect_availability(self) -> dict[str, bool]:
        availability: dict[str, bool] = {}
        for spec in CAPABILITY_SPECS:
            module = self.root / spec.module_path
            try:
                source = module.read_text(encoding="utf-8")
            except OSError:
                availability[spec.id] = False
            else:
                availability[spec.id] = spec.evidence in source
        return availability

    def project(self) -> HermesProjectionResult:
        identity = self.inspect_identity()
        availability = self.inspect_availability()
        source = Source(
            kind="git-submodule",
            repository=identity.repository,
            revision=identity.revision,
        )
        descriptors = tuple(
            CapabilityDescriptor(
                id=spec.id,
                name=spec.name,
                version=identity.version,
                source=source,
                enabled=True,
                health=Health(
                    state=HealthState.UNKNOWN,
                    check="hermes-source-projection",
                    timeout_seconds=1.0,
                ),
                permissions=spec.permissions,
                dependencies=("core.hermes.agent",),
                entrypoint=Entrypoint(
                    kind=spec.entrypoint_kind,
                    address=spec.entrypoint_address,
                    options={"address_field": _address_field(spec.entrypoint_kind)},
                ),
                capability_type=spec.capability_type,
                configuration_requirements=ConfigurationRequirements(
                    required=spec.configuration_required,
                    optional=spec.configuration_optional,
                ),
                metadata={
                    "description": (
                        "Read-only JL projection of an upstream Hermes capability"
                    )
                },
            )
            for spec in CAPABILITY_SPECS
        )
        return HermesProjectionResult(
            identity=identity,
            registry=CapabilityRegistry(schema_version=1, capabilities=descriptors),
            availability=availability,
        )


def _git_revision(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise HermesProjectionError("cannot determine Hermes Git revision") from error
    return completed.stdout.strip()


def _packaged_or_git_revision(root: Path) -> str:
    """Read the build-time pin when the packaged bundle has no .git metadata."""
    marker = root / ".jl-revision"
    if marker.exists():
        try:
            revision = marker.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as error:
            raise HermesProjectionError(
                "cannot read packaged Hermes revision"
            ) from error
        if len(revision) != 40 or any(
            character not in "0123456789abcdef" for character in revision
        ):
            raise HermesProjectionError("invalid packaged Hermes revision")
        return revision
    return _git_revision(root)


def _address_field(kind: EntrypointKind) -> str:
    return "tool" if kind is EntrypointKind.HERMES_TOOL else "plugin"
