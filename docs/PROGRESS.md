# Phase 3A progress

Last updated: 2026-09-13 ICT

## Phase 3A current state

- Step 1 — Local IPC is complete and focused tests pass.
- The boundary is an AF_UNIX socket with a strict versioned JSON envelope,
  request/caller/session identity, bounded frames and timeouts, user-only
  runtime permissions, safe stale-socket recovery, and graceful shutdown.
- The transport validates and dispatches envelopes only; it executes no Hermes
  action or tool.
- Step 2 — IPC Authentication is complete and focused tests pass. A
  runtime-generated credential is stored in a user-only file, compared safely,
  rotated/recreated explicitly, and checked before privileged dispatch. The
  storage provider contract remains replaceable by a future Keychain provider.
- Step 3 — One-time Approval Store is complete and focused tests pass. Exact
  Phase 2 fingerprints are bound to caller/session, expire within a bounded
  TTL, can be revoked, and are consumed atomically once with replay rejection.
- Step 4 — Deterministic Request State Machine is complete and focused tests
  pass. Explicit transitions enforce authentication before policy, policy
  before approval, exact one-time approval for `CONFIRM`, permanent blocking
  for `DENY`, and inert preparation for `ALLOW`.
- Phase 3A implementation, validation, diff review, report, and fresh-context
  handoff are complete. Phase 3B execution is not implemented.

## Phase 2 baseline

- Phase 2 is complete through implementation, integration, lightweight
  validation, and documentation.
- Hermes remains the single core and is still a clean submodule at
  `044a77b3b6af4ce16138d42762f812a20b9f7a89` (`0.21.2`).
- JL owns only the control contracts under `src/jl_agent/control/`.
- The runtime dependency set is one package: `PyYAML>=6.0,<7`. Ruff and `ty`
  are pinned as optional development dependencies.
- No provider credentials, paid requests, local models, SwiftUI, voice, or
  computer-control implementation were added.

## Phase 3A sequential status

| Step | Status | Evidence |
|---|---|---|
| 1 — Local IPC | COMPLETE | 8 focused tests cover AF_UNIX round trip, ownership/modes, schema/version rejection, size/timeout bounds, active/stale endpoints, and shutdown. |
| 2 — Authentication | COMPLETE | 4 focused tests cover valid, missing, invalid, malformed, rotation/recreation, private modes, and fail-closed dispatch. |
| 3 — Approval Store | COMPLETE | 5 focused tests cover exact match, replay, expiry, revocation, caller/session/action changes, and atomic concurrent consumption. |
| 4 — State Machine | COMPLETE | 7 focused/integration tests cover legal transitions, auth/policy gates, allow/deny/confirm paths, replay, identity mismatch, and real socket-to-projection flow. |
| 5 — Closeout | COMPLETE | Complete diff/scope/secret/artifact review, `PHASE3A_REPORT.md`, `PHASE3A_HANDOFF.md`, and final Git/submodule checks. |

## Phase 2 sequential status

| Step | Status | Evidence |
|---|---|---|
| 1 — Capability Registry | COMPLETE | Strict schema-v1 YAML parser, immutable descriptors, duplicate-key/ID rejection, focused fixture tests. Commit `4f00221`. |
| 2 — Hermes Capability Projection | COMPLETE | Read-only projection for files, terminal, browser, memory, and MCP; validates pinned source identity and static availability without importing Hermes. Commit `cb80f1c`. |
| 3 — Health Monitor | COMPLETE | Cheap deterministic `healthy`, `degraded`, `unavailable`, `misconfigured`, and `disabled` evaluation; no service startup. Commit `bf5d03c`. |
| 4 — Permission / Risk Engine | COMPLETE | Six action classes, allow/confirm/deny decisions, descriptor-scope enforcement, upstream-denial preservation, and exact-action fingerprints. Commit `2f0cc11`. |
| 5 — Deterministic Model Router | COMPLETE | Rule-based hard filtering, task-profile ordering, privacy/locality/cost checks, explicit selection, and bounded fallback using fake candidates. Commit `721da01`. |
| 6 — Integration | COMPLETE | End-to-end preparation path reaches a Hermes tool/provider reference without invoking or duplicating the Hermes loop. Commit `0ca5bbe`. |
| 7 — Validation | COMPLETE | 30 JL tests, Ruff, `ty`, dependency check, source pin, secret scan, and diff checks pass. Commit `1064dd6`. |
| 8 — Documentation and handoff | COMPLETE | Phase 2 report/handoff and contract status updates prepared for a fresh Phase 3 context. |

## Phase 3A validation summary

The final lightweight validation uses a temporary development environment and
runs sequentially:

```bash
python -m unittest discover -s tests
ruff check src tests
ty check
python -m pip check
git submodule status
git -C upstream/hermes-agent status --short
git diff --check
```

Result: 54 tests passed; Ruff, `ty`, dependency, pin, secret-pattern, submodule,
and diff checks passed. The approximately 41,000-test Hermes suite was not run.

## Next action

Phase 3A stops at an inert, policy-approved Hermes invocation reference. Do not
start Phase 3B or add Hermes execution until its scope is explicitly approved.
The native approval surface, Keychain integration, audit UI, SwiftUI, voice,
computer control, LaunchAgent, and local models remain out of scope.
