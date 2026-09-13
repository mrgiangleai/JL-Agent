from __future__ import annotations

import unittest
from pathlib import Path

from jl_agent.control.registry import (
    CapabilityRegistry,
    CapabilityType,
    HealthState,
    RegistryValidationError,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "config" / "capabilities.example.yaml"


class CapabilityRegistryTests(unittest.TestCase):
    def test_example_contract_loads(self) -> None:
        registry = CapabilityRegistry.from_file(FIXTURE)

        self.assertEqual(registry.schema_version, 1)
        self.assertEqual(len(registry.capabilities), 1)
        capability = registry.get("core.hermes.agent")
        self.assertEqual(capability.version, "0.21.2")
        self.assertEqual(capability.capability_type, CapabilityType.AGENT_CORE)
        self.assertEqual(capability.health.state, HealthState.UNKNOWN)
        self.assertEqual(capability.entrypoint.kind, "executable")
        self.assertEqual(capability.entrypoint.address, ".venv/bin/hermes")
        self.assertEqual(capability.configuration_requirements.required, ())

    def test_duplicate_capability_ids_fail_closed(self) -> None:
        text = FIXTURE.read_text(encoding="utf-8")
        descriptor = text.split("capabilities:\n", maxsplit=1)[1]

        with self.assertRaisesRegex(
            RegistryValidationError, "duplicate capability IDs"
        ):
            CapabilityRegistry.from_yaml(text + descriptor)

    def test_missing_required_field_is_rejected(self) -> None:
        text = FIXTURE.read_text(encoding="utf-8").replace(
            "    permissions:\n", "    omitted_permissions:\n", 1
        )

        with self.assertRaisesRegex(RegistryValidationError, "unknown fields"):
            CapabilityRegistry.from_yaml(text)

    def test_invalid_enum_and_boolean_are_rejected(self) -> None:
        text = FIXTURE.read_text(encoding="utf-8")
        bad_type = text.replace("capability_type: agent-core", "capability_type: robot")
        bad_enabled = text.replace("enabled: true", "enabled: yes-please")

        with self.assertRaisesRegex(RegistryValidationError, "capability_type"):
            CapabilityRegistry.from_yaml(bad_type)
        with self.assertRaisesRegex(RegistryValidationError, "enabled"):
            CapabilityRegistry.from_yaml(bad_enabled)

    def test_duplicate_yaml_keys_are_rejected(self) -> None:
        text = FIXTURE.read_text(encoding="utf-8").replace(
            "schema_version: 1", "schema_version: 1\nschema_version: 1", 1
        )

        with self.assertRaisesRegex(RegistryValidationError, "duplicate YAML key"):
            CapabilityRegistry.from_yaml(text)

    def test_source_requires_identity_and_immutable_version(self) -> None:
        text = FIXTURE.read_text(encoding="utf-8")
        without_repository = text.replace(
            "      repository: https://github.com/NousResearch/hermes-agent.git\n", ""
        )
        without_revision = text.replace(
            "      revision: 044a77b3b6af4ce16138d42762f812a20b9f7a89\n", ""
        )

        with self.assertRaisesRegex(RegistryValidationError, "repository or package"):
            CapabilityRegistry.from_yaml(without_repository)
        with self.assertRaisesRegex(RegistryValidationError, "revision or version"):
            CapabilityRegistry.from_yaml(without_revision)


if __name__ == "__main__":
    unittest.main()
