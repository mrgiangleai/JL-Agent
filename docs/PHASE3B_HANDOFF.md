# Phase 3B handoff

## Read this first

Phase 3B is complete. Do not redo Phases 1, 2, 3A, or 3B, and do not start
Phase 4 without a new explicit brief. Read in this order:

1. `docs/PHASE3B_REPORT.md`
2. `docs/ARCHITECTURE.md`
3. `docs/SECURITY_MODEL.md`
4. `docs/CAPABILITY_REGISTRY.md`
5. `docs/MODEL_ROUTER.md`
6. `docs/PHASE3A_HANDOFF.md`
7. `docs/PROGRESS.md`

## Source state

- Branch: `main`, tracking `origin/main`.
- Hermes path: `upstream/hermes-agent`.
- Required clean Hermes revision:
  `044a77b3b6af4ce16138d42762f812a20b9f7a89` (`0.21.2`).
- Expected closeout: local `main == origin/main`, clean working tree, clean
  Hermes submodule. Revalidate these facts in a fresh context.

## Implemented boundary

```text
private AF_UNIX envelope
  -> runtime credential authentication
  -> strict codec with envelope-owned caller/session
  -> Phase 2 capability health and permission policy
  -> ALLOW or exact one-time approval consumption
  -> deterministic route and exact HermesInvocationProjection
  -> in-memory single-use prepared registration
  -> fresh identity/health/policy/fingerprint/approval/route revalidation
  -> prepared -> executing
  -> gate-authorized thin adapter
  -> pinned Hermes invoke_tool middleware/guardrail/registry path
  -> completed | denied | failed
  -> private bounded metadata-only audit event
```

There is no raw IPC-to-Hermes path. A projection by itself is not durable
authorization, and repeated execution is rejected.

## Important paths

- `src/jl_agent/control/execution_adapter.py`: typed thin adapter and lazy
  production bridge to pinned `AIAgent` plus `invoke_tool`.
- `src/jl_agent/control/execution.py`: single-use preparation store, final
  revalidation, execution authority, result identity, and audit emission.
- `src/jl_agent/control/request_state.py`: authenticated prepare/execute state
  lifecycle.
- `src/jl_agent/control/codec.py`: strict production wire decoder; caller and
  session come only from the authenticated envelope.
- `src/jl_agent/control/audit.py`: allowlisted, private, bounded JSONL ledger.
- `src/jl_agent/runtime_service.py`: foreground service composition, readiness,
  startup, and shutdown.
- `tests/test_execution_adapter.py`, `tests/test_execution.py`,
  `tests/test_audit.py`, `tests/test_codec.py`, and
  `tests/test_runtime_service.py`: focused Phase 3B coverage.
- `docs/PHASE3B_REPORT.md`: complete rationale, validation, and limitations.

## Hermes boundary contract

At the pinned revision, the bridge configures `run_agent.AIAgent` with exact
provider/model/fallback/toolset/session constraints and calls
`agent.agent_runtime_helpers.invoke_tool()` for one prepared tool. That path
retains Hermes request/execution middleware, pre-tool hooks, guardrails,
enabled-tool validation, registry dispatch, and native tool implementation.

Do not patch `run_agent.py`. Do not copy provider fallback, tools, memory,
sessions, approvals, or agent-loop logic. A pin upgrade requires inspecting the
constructor, `invoke_tool` signature, middleware/guardrail path, and toolset
names before changing the recorded revision.

## Security contracts to preserve

- Authentication precedes decode/policy/execution; caller/session identity is
  envelope-owned and checked again before execution.
- `permissions.py` remains the only action fingerprint authority.
- `DENY` and unapproved `CONFIRM` never execute; upstream denial is never
  weakened.
- Approval is exact, short-lived, consumed once, and unavailable for issuance
  or activation over normal IPC.
- Preparation expires after 30 seconds by default, is bounded in memory, and
  is single-use.
- Fresh capability version/health and deterministic route must exactly match
  the prepared projection.
- The adapter runs only an internal gate-issued command and one allowlisted
  action in the projected Hermes toolset.
- Audit metadata is allowlisted. Never log credentials, tokens, raw approvals,
  action arguments, user content, or hidden reasoning.
- Runtime directory/file/socket modes remain private; stale recovery must never
  delete an unowned, non-private, active, or wrong-type path.
- No public TCP listener, unattended destructive/high-risk execution, or
  direct Hermes bypass may be introduced.

## Runtime and validation

The development entry point is `jl-agent-runtime` after installing the project.
It runs in the foreground and defaults to
`~/Library/Application Support/JL Agent/runtime`. `--runtime-dir` may point to
an isolated development directory. No approval issuer is exposed by this
service, so confirmation-required actions need a future trusted consent client.

Use Python 3.11-3.13. With the small ignored development environment available,
run sequentially:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check src tests
.venv/bin/ty check
.venv/bin/python -m pip check
git diff --check
git submodule status --recursive
git -C upstream/hermes-agent status --short
```

Unix-socket tests may need a normal local macOS process when the command sandbox
blocks AF_UNIX creation. Do not bootstrap Hermes or run its approximately
41,000 tests unless a later pin change genuinely requires it.

## Deferred boundary

Phase 4 should begin only when explicitly scoped. The recommended starting
point is a native macOS lifecycle/consent client around the current IPC, then a
Keychain-backed credential provider. SwiftUI UI details, audit/history UI,
voice, wake word, computer control, OS permission flows, LaunchAgent, local
models, cloud backend, accounts, and distribution remain unimplemented.
