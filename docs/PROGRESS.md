# Phase 3B progress

Last updated: 2026-09-13 ICT

## Phase 3B current state

- The pinned Hermes execution surface was audited without modifying upstream.
- A thin adapter preserves exact provider/model/fallback/tool/session/arguments
  and dispatches through Hermes middleware and native tool registry.
- The final gate revalidates identity, TTL, fingerprint, approval, capability
  version/health, upstream denial, and route immediately before execution.
- Lifecycle supports `prepared -> executing -> completed`, with terminal
  `denied`/`failed` and rejection of repeated execution.
- A private bounded JSONL ledger records allowlisted security metadata only.
- A foreground local runtime composes IPC, authentication, policy, approvals,
  execution, Hermes adapter, audit, readiness, and graceful shutdown.
- Phase 3B code, adversarial tests, documentation, and validation are complete.

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

## Phase 3B sequential status

| Step | Status | Evidence |
|---|---|---|
| 1 — Hermes surface audit | COMPLETE | Pinned `AIAgent` constructor and `invoke_tool` dispatch inspected statically. |
| 2 — Execution adapter | COMPLETE | Exact translation, internal gate authority, normalized results, and deterministic fake tests. |
| 3 — Final execution gate | COMPLETE | Fresh policy/health/fingerprint/approval/route checks and fail-closed drift tests. |
| 4 — Lifecycle | COMPLETE | Explicit executing/completed transitions plus terminal and duplicate rejection tests. |
| 5 — Audit ledger | COMPLETE | Metadata allowlist, private modes, hashed approval reference, bounded retention, and event-sequence tests. |
| 6 — Runtime service | COMPLETE | User-local AF_UNIX composition, readiness, stale recovery, and graceful shutdown tests. |
| 7 — Closeout | COMPLETE | JL validation, secret/diff/pin/submodule checks, report, and fresh-context handoff. |

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

## Phase 3B validation summary

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

Result: 74 tests passed; Ruff, `ty`, dependency, pin, secret-pattern,
submodule, and diff checks passed. Tests make no live provider or paid calls.
The approximately 41,000-test Hermes suite was not run.

## Next action

Phase 3B is complete. Do not start Phase 4 without a new explicit brief. The
recommended next boundary is a native macOS lifecycle/consent client that uses
the existing authenticated IPC and never receives approval-issuance authority
through untrusted request payloads. Keychain, SwiftUI, voice, computer control,
LaunchAgent, local models, and audit UI remain deferred.
