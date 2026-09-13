# Phase 4A native trust boundary

Status: accepted before implementation.

## Existing contract

The Phase 3B runtime has one authenticated protocol-v1 `AF_UNIX` endpoint.
Normal callers may prepare or execute an exact `ControlRequest`; they cannot
issue or activate approvals. Caller and session identity come from the
authenticated envelope, and `permissions.py` is the only action-fingerprint
authority.

## Selected boundary

Phase 4A keeps the existing endpoint and adds a second, private consent socket.
The consent socket is not an alternate execution path. It accepts only an
opaque pending-consent ID, an approve/reject decision, and a signature from the
native app's Keychain-held RSA key.

The runtime creates every pending consent record after its own policy check.
That record owns the authoritative fingerprint, exact request, caller,
session, bounded safe summary, random nonce, and short expiry. The native app
cannot submit a fingerprint, action arguments, approval TTL, wildcard, or raw
approval. Its signature covers the runtime challenge, request identity,
caller/session, and exact decision.

On approval, the trusted handler looks up the pending record, verifies the
signature, issues and consumes one exact in-memory approval, prepares the
stored request, and registers it with the existing execution gate. Approval
tokens never cross either socket. Reject, expiry, identity mismatch, invalid
signature, replay, or changed request state fails closed.

```text
normal authenticated socket                 trusted consent socket
prepare exact request                       opaque consent ID + signed decision
        |                                               |
        v                                               v
policy -> pending consent record <- Keychain public-key verification
                    |
                    v
exact one-time approval -> existing preparation/execution gate -> Hermes
```

## Key material and enrollment

- The native signing private key is generated and retained by macOS Keychain;
  it is never exported, logged, displayed, or sent to the runtime.
- The corresponding public key is exported to a user-owned `0600` runtime
  file before runtime startup. The runtime loads and pins it for that process.
- Replacing the consent key requires explicit native rotation and runtime
  restart. A missing or invalid public key leaves normal low-risk requests
  available but marks trusted consent unavailable.
- The normal IPC bearer credential remains server-generated in its private
  runtime file. The native client imports it into Keychain and subsequently
  reads it only from Keychain. Rotation is an explicit stopped-runtime command
  followed by a native Keychain refresh.

The public-key enrollment file is not a secret. Its private ownership protects
against accidental or ordinary protocol-level callers; it is not claimed to
contain arbitrary code already running as the same macOS user. Stronger
same-user process isolation and production code-signing policy remain future
distribution work.

## Non-goals

The consent socket cannot execute tools, invoke Hermes, mutate capability
health, return credentials, list approvals, issue broad approvals, or accept a
client-computed action fingerprint. Phase 4A adds no TCP listener, daemon,
LaunchAgent, voice, computer control, or second agent runtime.
