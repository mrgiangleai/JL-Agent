from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from jl_agent.control.approvals import OneTimeApprovalStore
from jl_agent.control.audit import AuditLedger
from jl_agent.control.auth import FileCredentialProvider
from jl_agent.control.consent import (
    ConsentCoordinator,
    ConsentState,
    RSAPKCS1v15SHA256Verifier,
    TrustedConsentRequestHandler,
)
from jl_agent.control.control_plane import ControlRequest, JLControlPlane
from jl_agent.control.execution import ExecutionGate
from jl_agent.control.execution_adapter import (
    HermesExecutionAdapter,
    HermesExecutionStatus,
    HermesRuntimeRequest,
    HermesRuntimeResult,
)
from jl_agent.control.hermes_projection import HermesProjection
from jl_agent.control.ipc import PROTOCOL_VERSION, IPCRequestEnvelope
from jl_agent.control.permissions import ActionProposal
from jl_agent.control.registry import HealthState
from jl_agent.control.request_state import RequestState, SecureControlRequestHandler
from jl_agent.control.router import (
    CostClass,
    DeterministicModelRouter,
    ModelCandidate,
    RouteRequest,
    RouterPolicy,
    TaskCategory,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeRuntime:
    def __init__(self) -> None:
        self.requests: list[HermesRuntimeRequest] = []

    def run(self, request: HermesRuntimeRequest) -> HermesRuntimeResult:
        self.requests.append(request)
        return HermesRuntimeResult(HermesExecutionStatus.COMPLETED, "safe")


class DigestVerifier:
    available = True
    key_fingerprint = "0" * 64

    def verify(self, message: bytes, signature: str) -> bool:
        expected = base64.b64encode(hashlib.sha256(message).digest()).decode()
        return signature == expected


class TrustedNativeConsentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        runtime_root = Path(self.temporary.name) / "runtime"
        self.credentials = FileCredentialProvider(runtime_root / "ipc.credential")
        self.credential = self.credentials.load_or_create()
        self.approvals = OneTimeApprovalStore()
        self.now = 1000.0
        self.coordinator = ConsentCoordinator(clock=lambda: self.now)
        self.control_plane = JLControlPlane(
            projection=HermesProjection(ROOT / "upstream" / "hermes-agent"),
            router=DeterministicModelRouter(RouterPolicy(max_attempts=2)),
        )
        self.runtime = FakeRuntime()
        self.audit = AuditLedger(runtime_root / "audit.jsonl")
        self.gate = ExecutionGate(
            control_plane=self.control_plane,
            approvals=self.approvals,
            adapter=HermesExecutionAdapter(self.runtime),
            audit=self.audit,
        )
        self.candidate = ModelCandidate(
            id="fake.local",
            provider="fake-provider",
            model="fake-model",
            abilities=frozenset({"text", "tool-calling"}),
            health=HealthState.HEALTHY,
            local=True,
            data_residency="device",
            cost_class=CostClass.FREE,
        )
        self.current_request = self.confirmation_request()

        def decode(_: IPCRequestEnvelope) -> ControlRequest:
            return self.current_request

        self.normal = SecureControlRequestHandler(
            credentials=self.credentials,
            approvals=self.approvals,
            control_plane=self.control_plane,
            request_decoder=decode,
            execution_gate=self.gate,
            consent_coordinator=self.coordinator,
        )
        self.trusted = TrustedConsentRequestHandler(
            coordinator=self.coordinator,
            verifier=DigestVerifier(),
            approvals=self.approvals,
            control_plane=self.control_plane,
            execution_gate=self.gate,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def confirmation_request(self) -> ControlRequest:
        return ControlRequest(
            capability_id="core.hermes.files",
            action=ActionProposal(
                action="write_file",
                normalized_arguments={"path": "/outside/file", "content": "safe"},
                requested_permissions=("local.write.reversible",),
                resolved_target="/outside/file",
                caller="native-app",
                session="session-1",
                target_within_workspace=False,
            ),
            route=RouteRequest(
                category=TaskCategory.SIMPLE,
                required_abilities=frozenset({"text", "tool-calling"}),
                local_only=True,
                off_device_allowed=False,
            ),
            candidates=(self.candidate,),
        )

    def normal_envelope(self, operation: str) -> IPCRequestEnvelope:
        return IPCRequestEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id="request-1",
            caller_id="native-app",
            session_id="session-1",
            operation=operation,
            payload={},
            credential=self.credential,
        )

    def consent_envelope(
        self,
        consent: dict[str, object],
        decision: str,
        *,
        session_id: str = "session-1",
        extra: dict[str, object] | None = None,
    ) -> IPCRequestEnvelope:
        message = bytearray(b"jl-agent-consent-v1\0")
        for field in (
            str(consent["consent_id"]),
            str(consent["request_id"]),
            str(consent["caller_id"]),
            str(consent["session_id"]),
            decision,
            str(consent["nonce"]),
        ):
            encoded = field.encode()
            message.extend(len(encoded).to_bytes(8, "big"))
            message.extend(encoded)
        signature = base64.b64encode(hashlib.sha256(message).digest()).decode()
        payload: dict[str, object] = {
            "consent_id": consent["consent_id"],
            "decision": decision,
            "signature": signature,
        }
        payload.update(extra or {})
        return IPCRequestEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id="request-1",
            caller_id="native-app",
            session_id=session_id,
            operation="consent-decision",
            payload=payload,
            credential=None,
        )

    def prepare_consent(self) -> dict[str, object]:
        waiting = self.normal(self.normal_envelope("prepare"))
        self.assertTrue(waiting.ok)
        assert waiting.result is not None
        self.assertEqual(waiting.result["state"], "awaiting_approval")
        self.assertNotIn("approval_fingerprint", waiting.result)
        return dict(waiting.result["consent"])

    def test_signed_exact_consent_prepares_then_executes_once(self) -> None:
        consent = self.prepare_consent()
        approved = self.trusted(self.consent_envelope(consent, "approve"))
        executed = self.normal(self.normal_envelope("execute"))

        self.assertTrue(approved.ok)
        self.assertEqual(approved.result["state"], "prepared")  # type: ignore[index]
        self.assertTrue(executed.ok)
        self.assertEqual(len(self.runtime.requests), 1)

        replay = self.trusted(self.consent_envelope(consent, "approve"))
        self.assertEqual(replay.error_code, "consent_replayed")
        duplicate = self.normal(self.normal_envelope("execute"))
        self.assertEqual(duplicate.error_code, "duplicate_execution")
        self.assertEqual(len(self.runtime.requests), 1)

    def test_reject_never_prepares_or_executes(self) -> None:
        consent = self.prepare_consent()
        rejected = self.trusted(self.consent_envelope(consent, "reject"))
        execution = self.normal(self.normal_envelope("execute"))

        self.assertTrue(rejected.ok)
        self.assertEqual(rejected.result["state"], "denied")  # type: ignore[index]
        self.assertEqual(execution.error_code, "stale_preparation")
        self.assertEqual(self.runtime.requests, [])

    def test_expired_consent_denies_without_approval_or_execution(self) -> None:
        consent = self.prepare_consent()
        self.now += 31.0

        expired = self.trusted(self.consent_envelope(consent, "approve"))

        self.assertEqual(expired.error_code, "consent_expired")
        record = self.coordinator._records[str(consent["consent_id"])]
        self.assertEqual(record.state, ConsentState.EXPIRED)
        self.assertEqual(record.lifecycle.state, RequestState.DENIED)
        self.assertEqual(self.runtime.requests, [])
        self.assertEqual(len(self.approvals._records), 0)
        event = self.audit.read()[-1]
        self.assertEqual(event["event"], "request_denied")
        self.assertEqual(event["error_category"], "consent_expired")

        replay = self.trusted(self.consent_envelope(consent, "approve"))
        self.assertEqual(replay.error_code, "consent_expired")
        self.assertEqual(self.runtime.requests, [])

    def test_wrong_identity_wildcard_and_invalid_signature_fail_closed(self) -> None:
        consent = self.prepare_consent()
        wrong_session = self.trusted(
            self.consent_envelope(consent, "approve", session_id="other")
        )
        self.assertEqual(wrong_session.error_code, "identity_mismatch")

        wildcard = self.trusted(
            self.consent_envelope(
                consent,
                "approve",
                extra={"binding_fingerprint": "*"},
            )
        )
        self.assertEqual(wildcard.error_code, "malformed_payload")

        invalid = replace(
            self.consent_envelope(consent, "approve"),
            payload={
                "consent_id": consent["consent_id"],
                "decision": "approve",
                "signature": "invalid",
            },
        )
        denied = self.trusted(invalid)
        self.assertEqual(denied.error_code, "invalid_consent_signature")
        self.assertEqual(self.runtime.requests, [])

    def test_changed_action_requires_new_consent(self) -> None:
        consent = self.prepare_consent()
        approved = self.trusted(self.consent_envelope(consent, "approve"))
        self.assertTrue(approved.ok)

        self.current_request = replace(
            self.current_request,
            action=replace(
                self.current_request.action,
                normalized_arguments={"path": "/outside/changed", "content": "safe"},
                resolved_target="/outside/changed",
            ),
        )
        changed = self.normal(self.normal_envelope("execute"))
        self.assertEqual(changed.error_code, "stale_preparation")
        self.assertEqual(self.runtime.requests, [])


class NativeConsentSignatureVerifierTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("openssl"), "system OpenSSL is required")
    def test_standard_rsa_keychain_compatible_signature_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private_key = root / "private.pem"
            public_key = root / "public.der"
            message = root / "message.bin"
            signature = root / "signature.bin"
            subprocess.run(
                [
                    "openssl",
                    "genpkey",
                    "-algorithm",
                    "RSA",
                    "-pkeyopt",
                    "rsa_keygen_bits:2048",
                    "-out",
                    str(private_key),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "openssl",
                    "rsa",
                    "-in",
                    str(private_key),
                    "-RSAPublicKey_out",
                    "-outform",
                    "DER",
                    "-out",
                    str(public_key),
                ],
                check=True,
                capture_output=True,
            )
            os.chmod(public_key, 0o600)
            message.write_bytes(b"keychain-compatible-consent-message")
            subprocess.run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-sign",
                    str(private_key),
                    "-out",
                    str(signature),
                    str(message),
                ],
                check=True,
                capture_output=True,
            )

            verifier = RSAPKCS1v15SHA256Verifier.from_file(public_key)
            encoded = base64.b64encode(signature.read_bytes()).decode()

            self.assertTrue(verifier.verify(message.read_bytes(), encoded))
            self.assertFalse(verifier.verify(b"changed", encoded))
            self.assertEqual(
                verifier.key_fingerprint,
                hashlib.sha256(public_key.read_bytes()).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
