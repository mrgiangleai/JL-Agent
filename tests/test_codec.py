from __future__ import annotations

import unittest
from typing import cast

from jl_agent.control.codec import decode_control_request
from jl_agent.control.ipc import PROTOCOL_VERSION, IPCRequestEnvelope


def payload() -> dict[str, object]:
    return {
        "capability_id": "core.hermes.files",
        "action": {
            "action": "read_file",
            "normalized_arguments": {"path": "docs/ARCHITECTURE.md"},
            "requested_permissions": ["local.read"],
        },
        "route": {
            "category": "simple",
            "required_abilities": ["text", "tool-calling"],
            "local_only": True,
            "off_device_allowed": False,
        },
        "candidates": [
            {
                "id": "fake.local",
                "provider": "fake-provider",
                "model": "fake-model",
                "abilities": ["text", "tool-calling"],
                "health": "healthy",
                "local": True,
                "data_residency": "device",
                "cost_class": 0,
            }
        ],
    }


def envelope(value: dict[str, object]) -> IPCRequestEnvelope:
    return IPCRequestEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id="request-1",
        caller_id="native-app",
        session_id="session-1",
        operation="prepare",
        payload=value,
        credential="runtime-credential",
    )


class ControlRequestCodecTests(unittest.TestCase):
    def test_decodes_identity_from_authenticated_envelope(self) -> None:
        request = decode_control_request(envelope(payload()))
        self.assertEqual(request.action.caller, "native-app")
        self.assertEqual(request.action.session, "session-1")
        self.assertEqual(
            request.action.normalized_arguments["path"],
            "docs/ARCHITECTURE.md",
        )
        self.assertEqual(request.candidates[0].model, "fake-model")

    def test_unknown_or_malformed_fields_fail_closed(self) -> None:
        unknown = payload()
        unknown["issue_approval"] = True
        with self.assertRaises(ValueError):
            decode_control_request(envelope(unknown))

        malformed = payload()
        malformed["candidates"] = "not-an-array"
        with self.assertRaises(ValueError):
            decode_control_request(envelope(malformed))

    def test_caller_cannot_supply_a_different_action_identity(self) -> None:
        value = payload()
        action = cast(dict[str, object], value["action"])
        action["caller"] = "forged-caller"
        with self.assertRaises(ValueError):
            decode_control_request(envelope(value))


if __name__ == "__main__":
    unittest.main()
