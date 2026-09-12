"""Stable control-layer contracts shared by JL integrations."""

from .registry import (
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilityType,
    ConfigurationRequirements,
    Entrypoint,
    EntrypointKind,
    Health,
    HealthState,
    RegistryValidationError,
    Source,
)

__all__ = [
    "CapabilityDescriptor",
    "CapabilityRegistry",
    "CapabilityType",
    "ConfigurationRequirements",
    "Entrypoint",
    "EntrypointKind",
    "Health",
    "HealthState",
    "RegistryValidationError",
    "Source",
]
