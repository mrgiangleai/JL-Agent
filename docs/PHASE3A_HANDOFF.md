# Phase 3A handoff

## Read this first

Phase 3A is complete. Do not redo Phase 1, Phase 2, or Phase 3A. Before any
Phase 3B work, read in this order:

1. `docs/PHASE3A_REPORT.md`
2. `docs/ARCHITECTURE.md`
3. `docs/SECURITY_MODEL.md`
4. `docs/CAPABILITY_REGISTRY.md`
5. `docs/MODEL_ROUTER.md`
6. `docs/PHASE2_HANDOFF.md`
7. `docs/PROGRESS.md`

Do not begin Phase 3B until its explicit scope is provided. Execution was
deliberately excluded from Phase 3A.

## Source state

- Branch: `main`, tracking `origin/main`.
- Hermes path: `upstream/hermes-agent`.
- Required Hermes revision:
  `044a77b3b6af4ce16138d42762f812a20b9f7a89`.
- Hermes package version: `0.21.2`.
- Expected closeout state: local `main` equals `origin/main`, the working tree
  is clean, and the Hermes submodule is clean at the required revision.

Always revalidate this state rather than assuming it from this document.

## Phase 3A boundary

The implemented path is:

```text
AF_UNIX envelope
  -> runtime credential authentication
  -> caller/session identity match
  -> Phase 2 capability health and permission policy
  -> ALLOW or exact one-time approval consumption
  -> deterministic model routing
  -> inert HermesInvocationProjection
```

There is no step after `prepared`. No Hermes tool, provider, model, subprocess,
or side effect is invoked.

## Important paths

- `src/jl_agent/control/ipc.py`: version-1 Unix-socket framing, limits,
  endpoint ownership, stale recovery, and lifecycle.
- `src/jl_agent/control/auth.py`: runtime file credential and replaceable
  provider interface for future Keychain integration.
- `src/jl_agent/control/approvals.py`: exact-fingerprint one-time approvals,
  TTL, revocation, expiry, and concurrency-safe consumption.
- `src/jl_agent/control/request_state.py`: authenticated request lifecycle and
  secure control-plane handler.
- `src/jl_agent/control/control_plane.py`: Phase 2 policy check plus inert
  preparation for direct allow or consumed confirmation.
- `src/jl_agent/control/permissions.py`: authoritative approval fingerprint;
  do not create another action identity or weaker fingerprint.
- `tests/test_ipc.py`, `tests/test_auth.py`, `tests/test_approvals.py`, and
  `tests/test_request_state.py`: Phase 3A focused coverage.
- `docs/PHASE3A_REPORT.md`: implementation, validation, scope review, and known
  limitations.

## Contracts to preserve

- Protocol version is `1`; default request size is 64 KiB and default request
  timeout is two seconds.
- IPC is local-only `AF_UNIX`; runtime directory and socket modes are `0700`
  and `0600`.
- Session IDs are routing identities, not credentials. Envelope caller/session
  must match the `ActionProposal` values included in the exact fingerprint.
- Runtime credentials are generated, private, safely compared, rotatable, and
  never logged or committed.
- Approval lifecycle is `issued -> available -> consumed`, with `expired` and
  `revoked` terminal states. TTL is capped at five minutes.
- Approval issuance/activation remains unavailable over IPC. Only a trusted
  future consent surface may call those APIs.
- `MUST_BE_DENIED` never reaches `prepared`.
- `REQUIRES_CONFIRMATION` reaches `prepared` only after exact atomic approval
  consumption and a fresh policy check.
- Unknown scopes, unhealthy/disabled capabilities, upstream denial, identity
  mismatch, malformed input, missing auth, replay, and illegal state
  transitions fail closed.
- JL policy may make Hermes stricter but must never weaken Hermes or another
  upstream denial.

## Phase 3B guardrails

Phase 3B is expected to start from `HermesInvocationProjection`, but its exact
scope must come from a new explicit brief. Any execution adapter must use a
supported Hermes configuration, plugin, MCP, API, or subprocess boundary. It
must not patch or duplicate the Hermes agent loop, provider fallback, tool
registry, memory/session state, or approval implementation.

Before an actual side effect, revalidate all security-relevant state required
by the Phase 3B brief. A `prepared` object is not durable authorization and
must not become a reusable or wildcard execution grant. Preserve Hermes'
defense-in-depth checks.

Do not add SwiftUI, voice, wake word, computer control, local models,
LaunchAgent, audit/history UI, external frameworks, or cloud services unless a
future phase explicitly scopes them.

## Known limitations handed to Phase 3B

- No execution state or Hermes execution adapter exists.
- The production wire decoder for a complete `ControlRequest` is still an
  injected trusted boundary.
- Credential storage is a private runtime file; Keychain is not implemented.
- Approvals are in memory and are invalidated by process restart.
- There is no native consent issuer, audit persistence, process containment,
  or runtime supervisor.
- The Unix-socket server handles connections serially; approval consumption is
  independently concurrency-safe.

These are documented gaps, not permission to expand Phase 3B beyond its future
brief.

## Lightweight verification

Use Python 3.11–3.13. If the ignored `.venv` is absent, recreate only the small
JL environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Then run sequentially:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check src tests
.venv/bin/ty check
.venv/bin/python -m pip check
git diff --check
git submodule status --recursive
git -C upstream/hermes-agent status --short
```

Do not bootstrap Hermes or run its approximately 41,000 tests unless a future
change actually modifies the upstream boundary. Unix-socket tests may require a
normal local macOS process when a command sandbox blocks socket creation.
