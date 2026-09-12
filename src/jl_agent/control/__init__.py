"""Stable control-layer contracts shared by JL integrations."""

from .hermes_projection import (
    HermesIdentity,
    HermesProjection,
    HermesProjectionError,
    HermesProjectionResult,
)
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
    "HermesIdentity",
    "HermesProjection",
    "HermesProjectionError",
    "HermesProjectionResult",
    "RegistryValidationError",
    "Source",
]
