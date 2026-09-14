# Phase 5 progress

Last updated: 2026-09-14 ICT

## Phase 5 current state

- Phase 5 deterministic implementation is complete through the pre-live gate.
- The pinned Hermes voice, VAD/STT/TTS, wake-word, ownership, pause/resume,
  bundled-model, and diagnostics surfaces were audited before coding.
- The minimal JL boundary is fixed: off by default, separately activation-gated,
  authenticated caller/session ownership, bounded in-memory events, no raw
  audio in IPC/audit, and an explicit empty Hermes toolset for every voice turn.
- The foreground runtime now composes the thin Hermes adapter; graceful shutdown
  releases voice and wake ownership.
- The SwiftUI client exposes strict authenticated status/start/stop/event
  controls and visibly states that voice tool execution is disabled. It does
  not capture audio or request TCC.
- Deterministic coordinator and IPC contract tests were written first. All 116
  backend and 15 native contract tests pass; release SwiftUI build, Ruff, ty,
  pip check, Swift format, and diff checks pass.
- No voice dependency/model was installed, no provider/live audio call was
  made, and Microphone TCC was not requested.
- Detailed evidence: `docs/PHASE5_HERMES_AUDIT.md`.

## Phase 5 sequential status

| Step | Status | Evidence |
|---|---|---|
| 1 — Hermes audit | COMPLETE | Pinned capture/VAD/STT/TTS/wake/ownership/model surfaces recorded; no duplicate engine. |
| 2 — JL voice boundary | COMPLETE | Two activation gates, one caller/session lease, bounded events, and graceful release. |
| 3 — Tool-free turn | COMPLETE | Hermes text turn receives explicit `toolsets=[]`; deterministic assertion passes. |
| 4 — Authenticated IPC | COMPLETE | Six strict protocol-v1 operations; malformed/cross-session requests fail closed. |
| 5 — Native client | COMPLETE | Strict models/client plus compact Voice + Wake Word controls; no native capture. |
| 6 — Deterministic validation | COMPLETE | 116 backend tests, 15 native tests, release build, lint/type/dependency/format/diff checks pass. |
| 7 — Dependency/model/TCC | BLOCKED ON APPROVAL | No install, download, live provider/audio, or Microphone action attempted. |

## Phase 4C completed state

- Exact pinned Hermes and official Cua source/release contracts were re-audited.
- A version/checksum/signature-pinned Cua Driver provisioning workflow passes
  repository-only preflight without installing or prompting for TCC.
- Runtime readiness now verifies executable reachability, full manifest,
  executable-derived signed app identity, both TCC grants, Hermes pin, JL
  policy/auth, and current consent enrollment.
- Native onboarding shows runtime PID, Cua identity/version, blocked reason,
  per-permission Settings actions, restart guidance, and Recheck.
- The foreground lifecycle has a stable repository entry point and read-only
  status command; duplicate ownership, stale recovery, and graceful shutdown
  remain fail-closed.
- Consent key loss no longer silently creates a replacement enrollment; explicit
  rotation and runtime restart remain required.
- Official Cua Driver v0.28.0 is installed under the signed
  `com.trycua.driver` / `YCK386LBJ7` identity; the user granted its required
  Accessibility and Screen Recording permissions.
- The authoritative readiness gate passed with current consent enrollment,
  healthy computer-use status, and `execution_ready=true`.
- Exact request `5ce6215c-2aab-4d80-9807-0b23a7d370b9` completed one read-only
  AX capture through the full production path in 18,619 ms. It was not retried.
- Direct gate-authorized dispatch no longer constructs an LLM `AIAgent`;
  expired consent terminates denied; only execute responses use the bounded
  90-second client deadline.
- Phase 4C is complete.

## Phase 4C sequential status

| Step | Status | Evidence |
|---|---|---|
| 1 — Host contract re-audit | COMPLETE | Pinned Hermes symbols plus official Cua source, process, manifest, signing, TCC, install, and build contracts recorded. |
| 2 — Safe provisioning workflow | COMPLETE | Exact v0.28.0 assets verified; official signed app and CLI installed through the reviewed workflow. |
| 3 — Host identity/signing | COMPLETE | Official Cua identity enforced; JL remains an ad-hoc local client and is not the TCC owner. |
| 4 — TCC onboarding | COMPLETE | User granted Accessibility and Screen Recording only to CuaDriver; no automated TCC mutation. |
| 5 — Runtime lifecycle | COMPLETE | Stable foreground entry point/status; existing stale/duplicate/graceful invariants preserved. |
| 6 — Consent stability | COMPLETE | Public fingerprint binding, lost-key refusal, exact replay defense, and explicit expired-consent denial. |
| 7 — Live readiness gate | COMPLETE | Hermes/auth/policy/consent/driver/app/signing/TCC all healthy; `execution_ready=true`. |
| 8 — Live GUI smoke | COMPLETE | One accepted AX capture reached backend `execution_completed` through JL, Hermes, and CuaDriver; no retry. |
| 9 — Adversarial tests | COMPLETE | Missing/incompatible/unreachable/wrong identity/TCC/lifecycle/enrollment/drift/replay/bypass cases covered deterministically. |
| 10 — Validation | COMPLETE | 106 backend and 13 native tests plus lint/type/dependency/build/sign/secret/diff checks pass. |
| 11 — Documentation | COMPLETE | Final report, progress, host setup, and fresh-context handoff record the accepted boundary. |

## Phase 4B current state

- The pinned Hermes `computer_use` source path is audited and reused unchanged.
- JL represents Accessibility and Screen Recording state explicitly and derives
  fail-closed capability health from a bounded driver/status probe.
- The SwiftUI app shows compact permission status, rationale, and safe System
  Settings links without prompting or changing TCC.
- `core.hermes.computer-use` is projected through the existing registry, policy,
  consent, execution gate, and Hermes adapter.
- Inner actions have authoritative minimum scopes; mutating input is exact,
  confirmation-bound, attended, and protected by fresh foreground context.
- A cross-process native-to-pinned-Hermes `capture(mode="ax")` proof passes with
  Hermes' deterministic noop backend. Real macOS driver/TCC validation is
  deferred because this host lacks the component.

## Phase 4A baseline

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

## Phase 4B sequential status

| Step | Status | Evidence |
|---|---|---|
| 1 — Pinned Hermes audit | COMPLETE | Exact registry/schema/backend/capture/input/guardrail/TCC/CuaDriver symbols documented without upstream changes. |
| 2 — Permission model | COMPLETE | Accessibility and Screen Recording states plus denied/notDetermined/unknown/unavailable/restart handling; no unproven Input Monitoring. |
| 3 — Native permission UI | COMPLETE | Compact status, rationale, CuaDriver identity guidance, and explicit System Settings links. |
| 4 — Capability integration | COMPLETE | `core.hermes.computer-use`, trusted runtime probe, driver/TCC/config/enabled health, and exact Hermes toolset projection. |
| 5 — Risk policy | COMPLETE | Authoritative action-to-scope mapping, protected read/input confirmation, stronger effect classes, attended mutation. |
| 6 — Target integrity | COMPLETE | Approval-bound app/target/foreground and final fail-closed `lsappinfo` recheck before input. |
| 7 — Safe proof | COMPLETE | Swift/auth/IPC/policy/signed consent/gate/adapter/pinned Hermes handler/AX noop capture passed. |
| 8 — Adversarial tests | COMPLETE | Missing TCC/driver, disabled/misconfigured, drift/replay/no-consent/forged authority/upstream denial/native exclusion covered. |
| 9 — Validation | COMPLETE | 99 backend tests, Ruff, ty, pip, 9 native tests, format, release app build/sign, proof, pin/submodule checks passed. |
| 10 — Documentation/closeout | COMPLETE | Report, audit, architecture/security/progress, fresh handoff, commit/push, and final Git checks. |

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

## Phase 4B validation summary

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

Result: 99 backend tests and 9 native tests passed; Ruff, `ty`, dependency and
Swift format checks, SwiftUI release app build/sign verification, and the
explicitly authorized cross-process pinned-Hermes noop proof passed. Tests make
no live provider or paid calls. Real cua-driver/TCC/UI automation was not run.
Secret, pin/submodule, Git sync, and artifact checks passed. The approximately
41,000-test Hermes suite was not run.

## Next action

Phase 5 implementation is in progress under the approved minimal boundary. Do
not repeat the accepted computer-use smoke. Do not install/download voice
dependencies or models, request Microphone TCC, enable voice-driven tools, or
run live audio without a separate explicit approval.
