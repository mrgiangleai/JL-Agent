# Phase 4A progress

Last updated: 2026-09-14 ICT

## Phase 4A current state

- A dependency-free SwiftUI app provides status, session, exact request,
  result/error, trusted consent, credential maintenance, and safe activity.
- The native client speaks bounded protocol-v1 AF_UNIX and stores its normal
  IPC credential in Keychain.
- A separate private consent socket verifies a Keychain RSA signature and can
  resolve only a runtime-owned exact pending request.
- Fingerprints, approval IDs, TTL, caller/session binding, and one-time
  consumption remain runtime-owned; normal IPC still has no approval issuer.
- Status and activity are authenticated, bounded, structured, and metadata-only.
- Backend and native deterministic tests, lint/type checks, and release app
  build/sign verification pass.

## Phase 3B baseline

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

## Phase 4A sequential status

| Step | Status | Evidence |
|---|---|---|
| 1 — Contract/trust audit | COMPLETE | Existing v1/auth/prepare/execute contracts inspected; separate signed exact-consent boundary documented before code. |
| 2 — SwiftUI shell | COMPLETE | One functional status/request/result/activity window and exact native consent sheet. |
| 3 — Native IPC client | COMPLETE | AF_UNIX-only framing, timeout/size bounds, strict structured response and identity validation. |
| 4 — Keychain credential | COMPLETE | Private runtime credential import/refresh and Keychain-only normal client reads; explicit stopped-runtime rotation. |
| 5 — Trusted consent | COMPLETE | Runtime-owned pending record, Keychain RSA signature, exact internal approval consumption, replay/change rejection. |
| 6 — Request/result/activity | COMPLETE | ALLOW/CONFIRM/DENY, gate execution, result/error, and safe ledger projection. |
| 7 — Validation | COMPLETE | 84 backend tests, 7 native tests, release app build/sign, and explicitly authorized cross-process consent smoke pass. |
| 8 — Documentation/closeout | COMPLETE | Report, trust boundary, architecture/security updates, handoff, secret/pin/submodule checks, and Git closeout completed. |

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

## Phase 4A validation summary

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

Result: 84 backend tests and 7 native tests passed; Ruff, `ty`, dependency and
Swift format checks, SwiftUI debug/release compile, development app sign
verification, and the explicitly authorized cross-process consent smoke passed.
Tests make no live provider or paid calls. Secret, pin/submodule, Git sync, and
artifact checks passed. The approximately 41,000-test Hermes suite was not run.

## Next action

Phase 4A is complete. Do not begin Phase 4B without a new explicit brief. The
recommended next boundary is production native lifecycle and stable signed-app
consent-key enrollment; voice, computer control, LaunchAgent, local models,
cloud services, accounts, telemetry, and distribution remain deferred until
explicitly scoped.
