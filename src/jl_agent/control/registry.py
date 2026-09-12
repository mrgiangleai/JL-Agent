"""Parser and validator for the JL Capability Registry contract."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Self

import yaml


class RegistryValidationError(ValueError):
    """Raised when a registry document violates the public contract."""


class CapabilityType(StrEnum):
    AGENT_CORE = "agent-core"
    TOOL = "tool"
    SKILL = "skill"
    MODEL_PROVIDER = "model-provider"
    MEMORY = "memory"
    SCHEDULER = "scheduler"
    UI = "ui"
    SERVICE = "service"


class HealthState(StrEnum):
    UNKNOWN = "unknown"
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    MISCONFIGURED = "misconfigured"
    DISABLED = "disabled"


class EntrypointKind(StrEnum):
    PLUGIN = "plugin"
    MCP = "mcp"
    IPC = "ipc"
    EXECUTABLE = "executable"
    HERMES_TOOL = "hermes-tool"


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise RegistryValidationError("mapping keys must be strings")
        if key in mapping:
            raise RegistryValidationError(f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RegistryValidationError(f"{path} must be an object")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegistryValidationError(f"{path} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RegistryValidationError(f"{path} must be an array")
    result = tuple(
        _string(item, f"{path}[{index}]") for index, item in enumerate(value)
    )
    if len(set(result)) != len(result):
        raise RegistryValidationError(f"{path} contains duplicate values")
    return result


def _required(data: dict[str, Any], key: str, path: str) -> Any:
    if key not in data:
        raise RegistryValidationError(f"{path}.{key} is required")
    return data[key]


@dataclass(frozen=True, slots=True)
class Source:
    kind: str
    repository: str | None = None
    package: str | None = None
    revision: str | None = None
    version: str | None = None

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> Self:
        data = _mapping(value, path)
        allowed = {"kind", "repository", "package", "revision", "version"}
        _reject_unknown(data, allowed, path)
        source = cls(
            kind=_string(_required(data, "kind", path), f"{path}.kind"),
            repository=_optional_string(data.get("repository"), f"{path}.repository"),
            package=_optional_string(data.get("package"), f"{path}.package"),
            revision=_optional_string(data.get("revision"), f"{path}.revision"),
            version=_optional_string(data.get("version"), f"{path}.version"),
        )
        if not (source.repository or source.package):
            raise RegistryValidationError(
                f"{path} requires a canonical repository or package"
            )
        if not (source.revision or source.version):
            raise RegistryValidationError(
                f"{path} requires an immutable revision or version"
            )
        return source


@dataclass(frozen=True, slots=True)
class Health:
    state: HealthState
    check: str
    timeout_seconds: float

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> Self:
        data = _mapping(value, path)
        _reject_unknown(data, {"state", "check", "timeout_seconds"}, path)
        try:
            state = HealthState(_required(data, "state", path))
        except (TypeError, ValueError) as error:
            raise RegistryValidationError(f"{path}.state is not supported") from error
        timeout = _required(data, "timeout_seconds", path)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
        ):
            raise RegistryValidationError(
                f"{path}.timeout_seconds must be a positive number"
            )
        return cls(
            state=state,
            check=_string(_required(data, "check", path), f"{path}.check"),
            timeout_seconds=float(timeout),
        )


@dataclass(frozen=True, slots=True)
class Entrypoint:
    kind: EntrypointKind
    address: str
    options: dict[str, Any] = field(default_factory=dict)

    ADDRESS_FIELDS: ClassVar[tuple[str, ...]] = (
        "tool",
        "command",
        "server",
        "socket",
        "plugin",
    )

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> Self:
        data = _mapping(value, path)
        _reject_unknown(data, {"kind", *cls.ADDRESS_FIELDS}, path)
        try:
            kind = EntrypointKind(_required(data, "kind", path))
        except (TypeError, ValueError) as error:
            raise RegistryValidationError(f"{path}.kind is not supported") from error
        present = [key for key in cls.ADDRESS_FIELDS if key in data]
        if len(present) != 1:
            raise RegistryValidationError(
                f"{path} requires exactly one invocation address field"
            )
        address_key = present[0]
        address = _string(data[address_key], f"{path}.{address_key}")
        return cls(kind=kind, address=address, options={"address_field": address_key})


@dataclass(frozen=True, slots=True)
class ConfigurationRequirements:
    required: tuple[str, ...]
    optional: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> Self:
        data = _mapping(value, path)
        _reject_unknown(data, {"required", "optional"}, path)
        required = _string_list(_required(data, "required", path), f"{path}.required")
        optional = _string_list(_required(data, "optional", path), f"{path}.optional")
        overlap = set(required).intersection(optional)
        if overlap:
            raise RegistryValidationError(
                f"{path} keys cannot be both required and optional: {sorted(overlap)}"
            )
        return cls(required=required, optional=optional)


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    id: str
    name: str
    version: str
    source: Source
    enabled: bool
    health: Health
    permissions: tuple[str, ...]
    dependencies: tuple[str, ...]
    entrypoint: Entrypoint
    capability_type: CapabilityType
    configuration_requirements: ConfigurationRequirements
    metadata: dict[str, Any] = field(default_factory=dict)

    REQUIRED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "name",
            "version",
            "source",
            "enabled",
            "health",
            "permissions",
            "dependencies",
            "entrypoint",
            "capability_type",
            "configuration_requirements",
        }
    )
    OPTIONAL_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "description",
            "platforms",
            "modalities",
            "cost_class",
            "data_boundary",
            "risk_class",
            "fallbacks",
            "maintainer",
        }
    )

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> Self:
        data = _mapping(value, path)
        _reject_unknown(data, cls.REQUIRED_FIELDS | cls.OPTIONAL_FIELDS, path)
        missing = sorted(cls.REQUIRED_FIELDS.difference(data))
        if missing:
            raise RegistryValidationError(f"{path} is missing fields: {missing}")

        capability_id = _string(data["id"], f"{path}.id")
        if not re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)+", capability_id):
            raise RegistryValidationError(
                f"{path}.id is not a stable dotted identifier"
            )
        enabled = data["enabled"]
        if not isinstance(enabled, bool):
            raise RegistryValidationError(f"{path}.enabled must be a boolean")
        try:
            capability_type = CapabilityType(data["capability_type"])
        except (TypeError, ValueError) as error:
            raise RegistryValidationError(
                f"{path}.capability_type is not supported"
            ) from error

        return cls(
            id=capability_id,
            name=_string(data["name"], f"{path}.name"),
            version=_string(data["version"], f"{path}.version"),
            source=Source.from_mapping(data["source"], f"{path}.source"),
            enabled=enabled,
            health=Health.from_mapping(data["health"], f"{path}.health"),
            permissions=_string_list(data["permissions"], f"{path}.permissions"),
            dependencies=_string_list(data["dependencies"], f"{path}.dependencies"),
            entrypoint=Entrypoint.from_mapping(
                data["entrypoint"], f"{path}.entrypoint"
            ),
            capability_type=capability_type,
            configuration_requirements=ConfigurationRequirements.from_mapping(
                data["configuration_requirements"],
                f"{path}.configuration_requirements",
            ),
            metadata={key: data[key] for key in cls.OPTIONAL_FIELDS if key in data},
        )


@dataclass(frozen=True, slots=True)
class CapabilityRegistry:
    schema_version: int
    capabilities: tuple[CapabilityDescriptor, ...]

    @classmethod
    def from_yaml(cls, text: str) -> Self:
        try:
            loaded = yaml.load(text, Loader=_UniqueKeyLoader)
        except RegistryValidationError:
            raise
        except yaml.YAMLError as error:
            raise RegistryValidationError(f"invalid YAML: {error}") from error
        root = _mapping(loaded, "registry")
        _reject_unknown(root, {"schema_version", "capabilities"}, "registry")
        schema_version = _required(root, "schema_version", "registry")
        if schema_version != 1 or isinstance(schema_version, bool):
            raise RegistryValidationError("registry.schema_version must be 1")
        raw_capabilities = _required(root, "capabilities", "registry")
        if not isinstance(raw_capabilities, list):
            raise RegistryValidationError("registry.capabilities must be an array")
        capabilities = tuple(
            CapabilityDescriptor.from_mapping(value, f"capabilities[{index}]")
            for index, value in enumerate(raw_capabilities)
        )
        ids = [capability.id for capability in capabilities]
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            raise RegistryValidationError(f"duplicate capability IDs: {duplicates}")
        return cls(schema_version=1, capabilities=capabilities)

    @classmethod
    def from_file(cls, path: str | Path) -> Self:
        return cls.from_yaml(Path(path).read_text(encoding="utf-8"))

    def get(self, capability_id: str) -> CapabilityDescriptor:
        for capability in self.capabilities:
            if capability.id == capability_id:
                return capability
        raise KeyError(capability_id)


def _optional_string(value: Any, path: str) -> str | None:
    return None if value is None else _string(value, path)


def _reject_unknown(
    data: dict[str, Any], allowed: set[str] | frozenset[str], path: str
) -> None:
    unknown = sorted(set(data).difference(allowed))
    if unknown:
        raise RegistryValidationError(f"{path} has unknown fields: {unknown}")
