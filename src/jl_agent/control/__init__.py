"""Stable control-layer contracts shared by JL integrations."""

from .health import HealthAssessment, HealthMonitor, ProbeOutcome
from .hermes_projection import (
    HermesIdentity,
    HermesProjection,
    HermesProjectionError,
    HermesProjectionResult,
)
from .permissions import (
    ActionClass,
    ActionProposal,
    DecisionOutcome,
    PermissionDecision,
    PermissionRiskEngine,
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
    "ActionClass",
    "ActionProposal",
    "CapabilityDescriptor",
    "CapabilityRegistry",
    "CapabilityType",
    "ConfigurationRequirements",
    "DecisionOutcome",
    "Entrypoint",
    "EntrypointKind",
    "Health",
    "HealthAssessment",
    "HealthMonitor",
    "HealthState",
    "HermesIdentity",
    "HermesProjection",
    "HermesProjectionError",
    "HermesProjectionResult",
    "ProbeOutcome",
    "PermissionDecision",
    "PermissionRiskEngine",
    "RegistryValidationError",
    "Source",
]
