# Phase 4A report

## Outcome

Phase 4A adds the smallest native macOS control/consent client around the
existing Phase 3B runtime. Hermes Agent remains the sole runtime core, unchanged
at `044a77b3b6af4ce16138d42762f812a20b9f7a89` (`0.21.2`). The SwiftUI app has no
Hermes import or direct tool surface; all execution still passes through the
Python policy and execution gate.

The app provides runtime state, current caller/session identity, exact action
input, ALLOW/CONFIRM/DENY presentation, result/error output, credential/key
maintenance actions, and a compact safe-activity list. It is functional and
deliberately unpolished.

## Native architecture

`macos-app/` is a dependency-free Swift package with three products:

- `JLAgentCore`: protocol models, bounded AF_UNIX transport, structured client,
  Keychain providers, and consent signing.
- `JLAgentApp`: one SwiftUI window and one native consent sheet.
- `JLAgentNativeTests`: seven deterministic focused contract/Keychain tests.

The release build script creates and ad-hoc signs a development app bundle in
ignored `.build` output. No Xcode project, third-party UI/network package,
voice, computer control, menu-bar agent, or daemon installer was added.

## IPC client

The client uses the existing newline-framed JSON protocol v1. Requests retain
the exact envelope fields and envelope-owned caller/session identity. The
Darwin client uses only `AF_UNIX`, nonblocking connect/send/receive polling, a
two-second deadline, one frame per connection, and 64 KiB request/response
bounds. It validates the exact response envelope, protocol version, request ID,
success/error shape, and typed result fields. Malformed, oversized, timed-out,
unsupported, mismatched, missing-credential, and authentication failures fail
closed.

The normal authenticated endpoint now also supports:

- `status` with ready/degraded state, transport, protocol, Hermes revision, and
  consent availability;
- `activity` with a maximum of 100 records and only timestamp, capability,
  action class, policy decision, and execution status.

No public TCP or alternate execution channel exists.

## Keychain credential design

The runtime still generates and validates its bearer credential through the
replaceable Phase 3A `CredentialProvider` boundary and constant-time comparison.
The native client verifies the runtime file is owned, regular, bounded, and
user-only before importing it into a `WhenUnlockedThisDeviceOnly` Keychain
generic-password item. Normal requests load only from Keychain. The value is
never displayed or logged.

Missing Keychain state may be recreated from the private runtime file. Runtime
rotation is an explicit stopped-runtime command:

```bash
jl-agent-runtime --rotate-credential
```

The native **Refresh Credential** action then replaces the Keychain item. The
runtime refuses the rotation command while either service socket exists.

## Trusted consent boundary

The boundary was documented before implementation in
`PHASE4A_TRUST_BOUNDARY.md`.

When policy returns `requires-confirmation`, the runtime stores a bounded
30-second pending record containing the original request, authoritative
`permissions.py` fingerprint, caller/session, random nonce, lifecycle, and safe
presentation fields. The normal response contains only an opaque consent ID,
nonce, and safe summary; it contains no approval ID and no client-supplied
fingerprint.

The native app generates a 2048-bit RSA private key in Keychain and exports only
its public key to a private runtime file. The runtime pins that key at startup.
The separate private consent socket accepts exactly `consent_id`, `decision`,
and `signature`. The signature binds the consent ID, request ID, caller,
session, approve/reject value, and nonce using RSA PKCS#1 v1.5 SHA-256 over an
unambiguous length-prefixed protocol-v1 message.

Approve makes one runtime-owned exact approval available, consumes it
immediately, prepares the stored request, and registers it with the existing
gate. Approval tokens never leave the process. Reject ends the lifecycle without
preparation. Invalid signatures, unknown fields/wildcards, expiry, replay,
identity mismatch, changed action, and duplicate execution fail closed.

## Request, result, and activity flow

The UI sends a protocol-v1 `ControlRequest` with one exact capability/action,
JSON argument object, permission scope, target, and deterministic validation
route. Direct ALLOW automatically executes the prepared request. CONFIRM opens
the native sheet with capability, action, action class, safe target, risk,
caller, and session. A signed approval resumes the stored request; rejection
never executes. DENY and structured failures remain visible in the result area.

The activity list reads the Phase 3B ledger through the runtime's safe
projection. The Swift decoder rejects unexpected activity fields so identity,
credentials, fingerprints, raw approvals, provider details, arguments, content,
and reasoning cannot appear through this surface.

## Validation

Validation is deterministic and uses no provider/network/model request:

```text
JL-owned unittest suite                 84 passed
Ruff                                    passed
ty                                      passed
Swift focused native contract tests     7 passed
Swift format lint                       passed
SwiftUI debug/release compilation       passed
development .app build/sign verify      passed
Swift-to-Python AF_UNIX consent smoke   passed
```

The backend tests cover authentication, missing/wrong credentials, status and
activity bounds, malformed protocol, exact signed consent, reject, changed
action, replay, wrong identity, wildcard rejection, audit redaction, direct
Hermes-source exclusion, and standard RSA verification. Native tests cover
Keychain import/refresh, Keychain-held signing, signature verification,
structured auth/protocol failures, response identity, exact consent fields, and
activity allowlisting.

The checkout volume does not support creating Unix sockets. With explicit
authorization, the combined Swift-process-to-Python-process smoke ran in one
exact temporary directory under `/private/tmp`. It covered normal authenticated
IPC, CONFIRM, Keychain RSA signing, the separate consent socket, one-time
approval, gated fake-Hermes execution, and safe activity. The harness removed
its exact temporary directory and test Keychain entries afterward.

## Limitations and deferred work

- The native request route uses a deterministic local validation candidate.
  Fake Hermes execution in tests proves the gate path without paid calls; real
  tool output depends on local Hermes/provider configuration.
- Consent key enrollment is local public-key trust-on-first-provisioning plus
  runtime restart. Production distribution should bind enrollment to a stable
  signed app requirement.
- Server bearer storage remains a private file; only the native copy is
  Keychain-backed. This avoids adding fragile Python Keychain dependencies.
- Runtime state is memory-only, each socket is serial, and lifecycle remains a
  foreground operator-started process.
- No GUI behavior was independently inspected; native validation is build and
  deterministic contract coverage.
- Voice, STT/TTS, wake word, computer control, macOS automation permissions,
  menu bar, LaunchAgent, local model, cloud backend, accounts, telemetry, and
  distribution remain intentionally unimplemented.

## Phase 4B recommendation

Phase 4B should begin only from a new brief. The recommended next slice is
production native lifecycle/onboarding and stable code-signing-based consent
key enrollment around this exact boundary. Do not start voice or computer
control until that lifecycle and trust bootstrap are finalized.
