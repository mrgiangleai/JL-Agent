# Phase 3A report

## Outcome

Phase 3A implements the secure local request boundary that must exist before JL
Agent can execute Hermes actions. It adds local IPC, IPC authentication,
short-lived one-time approval consumption, and a deterministic request state
machine. The boundary stops at an inert `HermesInvocationProjection`; it does
not execute Hermes, tools, models, providers, or side effects.

Hermes Agent remains the single runtime core, unchanged in the
`upstream/hermes-agent` submodule at
`044a77b3b6af4ce16138d42762f812a20b9f7a89` (`0.21.2`).

## Implementation

### Local IPC

`src/jl_agent/control/ipc.py` provides a version-1 JSON protocol over an
`AF_UNIX` stream socket.

- The request envelope has exact fields for protocol version, request ID,
  caller ID, session ID, operation, payload, and credential.
- Requests default to a 64 KiB maximum and a two-second timeout.
- Unknown/missing fields, invalid UTF-8/JSON, extra frames, empty frames,
  malformed identifiers, oversized requests, and unsupported versions fail
  closed with structured errors.
- The runtime directory must be owned by the current user with no group/world
  access; it is created as `0700`. The socket is `0600`.
- A stale endpoint is removed only when it is an owned Unix socket that refuses
  connection. Active sockets and non-socket paths are preserved and reject
  startup.
- Shutdown closes the listener and removes only the socket inode created by
  that server instance.
- The transport validates and dispatches envelopes only. It has no Hermes or
  tool execution path and exposes no TCP listener.

### IPC authentication

`src/jl_agent/control/auth.py` defines a replaceable `CredentialProvider`
contract and the Phase 3A `FileCredentialProvider`.

- Credentials are generated at runtime with `secrets.token_urlsafe(32)`.
- The storage directory is user-owned and private; the credential file must be
  a regular, user-owned `0600` file.
- Creation is exclusive, rotation uses an atomic replacement, missing storage
  can be recreated, and unsafe/malformed storage fails closed.
- Authentication uses `hmac.compare_digest` and returns only generic failure
  messages. Credentials are not logged or committed.
- The provider boundary can later be replaced with a macOS Keychain-backed
  implementation without changing request policy.

### One-time approval store

`src/jl_agent/control/approvals.py` implements an in-memory,
concurrency-safe lifecycle:

```text
issued -> available -> consumed
   |          |
   +----------+-> expired / revoked
```

- The store accepts only the exact 64-character SHA-256 fingerprint emitted by
  the Phase 2 Permission/Risk Engine.
- Each approval is also bound to exact caller and session identities.
- TTL is positive and capped at five minutes.
- Consumption is atomic under a lock. Replay, expiry, revocation, wrong
  fingerprint, wrong caller, and wrong session fail closed.
- A mismatch does not consume the legitimate approval.
- Issuance and activation are trusted internal APIs. IPC cannot issue or make
  an approval available, preventing wildcard or self-approval escalation.

### Deterministic request state machine

`src/jl_agent/control/request_state.py` enforces explicit legal transitions:

```text
received -> authenticated -> policy_checked
                               |       |
                               |       +-> approved -> prepared
                               +-> awaiting_approval -> approved -> prepared

terminal: denied, failed
```

- Credential validation occurs before payload decoding or privileged request
  processing.
- Envelope caller/session identity must exactly match the Phase 2
  `ActionProposal` identity.
- `MUST_BE_DENIED` cannot reach `prepared`.
- `REQUIRES_CONFIRMATION` cannot reach `prepared` without atomically consuming
  the exact approval.
- `MAY_PROCEED` follows the existing Phase 2 policy and router path.
- Policy is checked again after approval consumption before constructing the
  inert invocation projection.
- Illegal lifecycle transitions raise `IllegalRequestTransition`.

`src/jl_agent/control/control_plane.py` now separates policy checking from
route preparation and exposes `prepare_confirmed()` for a consumed, matching
approval. Existing Phase 2 `prepare()` behavior remains compatible.

## Validation evidence

Validation was lightweight, JL-owned, and sequential.

| Check | Result |
|---|---|
| JL-owned unit/integration tests | 54 passed |
| Local IPC focused tests | 8 passed |
| Authentication focused tests | 4 passed |
| Approval focused tests | 5 passed |
| State-machine focused/integration tests | 7 passed |
| Ruff `0.15.10` | Passed |
| `ty` `0.0.21` | Passed |
| Installed dependency validation | `pip check` passed |
| Secret-pattern scan | No matches |
| Whitespace/diff validation | Passed |
| Hermes submodule identity | Clean at the required pin |
| Full Hermes suite | Not run by design |

The integration coverage includes a real authenticated Unix-socket round trip
through Phase 2 policy to an inert Hermes reference. It invokes no Hermes code.

## Closeout review

- Accidental scope expansion: none found. Changes are limited to JL control
  contracts, focused tests, and contract documentation.
- Hermes modifications: none. No file in the submodule changed and the pin did
  not move.
- Dependencies: no runtime or development dependency was added.
- Secrets: no credential value, private key, token, or provider secret is
  committed. Tests generate temporary credentials at runtime.
- Debug/temporary content: no debug prints, breakpoints, TODO scaffolding,
  generated environment, cache, socket, credential file, or build artifact is
  included.
- Excluded Phase 3B work: no Hermes execution adapter, real tool execution,
  execution state, native UI, voice, computer control, LaunchAgent, local LLM,
  external framework, or cloud backend was added.

## Remaining limitations

- `prepared` means policy-approved metadata only; there is no execution path.
- The file credential provider is an interim runtime mechanism, not Keychain.
- Approval records are runtime-memory-only and intentionally disappear on
  restart.
- The native trusted consent surface that calls `issue()` and
  `make_available()` does not exist.
- `SecureControlRequestHandler` accepts an injected trusted request decoder;
  Phase 3A does not define a production wire codec for every `ControlRequest`
  field.
- IPC lifecycle supervision, LaunchAgent integration, audit persistence, and
  OS/process containment are not implemented.
