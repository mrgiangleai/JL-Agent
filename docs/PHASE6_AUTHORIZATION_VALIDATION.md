# Phase 6 — JL authorization/runtime integration checkpoint

Date: 2026-09-15. Deterministic validation complete; scheduler and UI remain
disabled. No Phase 6 completion, production activation, or GUI proof is claimed.

## Implemented

- `src/jl_agent/control/automation.py`: production JL authority, exact consent
  binding, durable grants, revoke/global stop, and occurrence admission receipts.
- `src/jl_agent/automation_runtime.py`: adapter implementing the validated Hermes
  `acquire(request)` hook. It returns only the fixed audited
  `print("JL_REMINDER_DONE")` program as immutable bytes.
- `src/jl_agent/runtime_service.py`: constructs an inert automation component
  sharing the existing trusted `OneTimeApprovalStore`; shutdown closes admission
  if bound. Automation errors do not prevent existing IPC/voice shutdown.
- Production policy and lifecycle tests added in JL. The previously reviewed
  three-file Hermes patch is byte-for-byte unchanged, verified against
  `docs/PHASE6_UPSTREAM.patch`. Hermes HEAD remains
  `044a77b3b6af4ce16138d42762f812a20b9f7a89`.

## Authority contract

Activation consumes an already-available exact one-time approval. It never
issues approval, makes approval available, resumes a job, or starts a scheduler.
Binding includes a domain tag, profile, job semantics, caller/session, validity
deadline, profile configuration digest and fixed script digest. Initial grants
require a paused job. Grant validity is bounded to 24 hours; one-time consent
keeps the existing five-minute maximum. No normal IPC activation endpoint or
native consent UI operation was added in this checkpoint.

Job semantics include reminder text, schedule, repeat limit, identity and every
supported effect-bearing field. Only audited Hermes lifecycle fields and the
consumed repeat counter are excluded. Unknown fields, non-local delivery,
models/tools/skills/monitors, custom scripts and unsupported schedule kinds deny.
Any semantic or profile-config change requires new exact consent.

The adapter binds only in an explicitly prepared worker with the matching
`HERMES_HOME`. It verifies the Hermes pin, module location, and SHA-256 of all
three validated hook files before registration. Main runtime construction does
not call `bind_worker`, import Hermes cron, create authority storage, launch a
worker or start a ticker. `scheduler_enabled` stays false; no enable API exists.

## Persistence and concurrency

The private authority directory has a single-process advisory lock and a SQLite
ledger using DELETE journaling and FULL synchronization. Tests use internal
APFS under disposable `/private/tmp/jl-production-policy-*` runtime homes.
Existing non-private/symlink storage is rejected. Missing ledger after prior
initialization denies instead of silently creating fresh authority.

SQLite transactions serialize grant/revoke/stop/admission. Each execution ID
and each `(job, occurrence)` can receive permission only once. Receipts record
admission, not effect success: `spent` means the permission cannot be reused,
not that delivery was confirmed. Hermes remains authoritative for job execution
results, schedules, claims, due selection and history.

On exclusive startup, leftover `inflight` receipts become `unknown`. They still
block replay. No retry job or new schedule is created. Fresh processes reuse
unexpired grants while revalidating configuration and script identity. Revoked
grants and global stop remain denied after restart. There is no global resume
operation in this checkpoint.

Revoke/stop before admission denies. After admission, revoke/stop reports the
in-flight count, blocks new admissions and does not pretend to undo an effect.
Shutdown reports draining rather than closing a live authority underneath its
lease. The runtime never supplies the hook with an unaudited script.

Ledger failure before admission yields no lease. Failure during receipt
finalization leaves a durable in-flight record that recovers as unknown; it
cannot authorize a retry. Storage failures latch the authority closed for the
process. Capacity limits (1,000 grants / 10,000 receipts) deny new admission
instead of pruning replay protection. This is authorization state, not a second
scheduler or copy of Hermes' job store.

## Validation results

```text
.venv/bin/python -B -m unittest discover -s tests  168 tests PASS
.venv/bin/ruff check src tests                    PASS
.venv/bin/ty check                               PASS
.venv/bin/python -m pip check                    PASS
git diff --check                                PASS
Hermes patch equality with prior review artifact PASS
```

Full suite ran in the authorized local context needed for existing AF_UNIX
tests. Counts: prior 147 tests, 20 production-authorization tests and one new
shutdown-failure test. Existing runtime-composition test also asserts that
automation remains unbound, disabled and creates no state directory.

| Required behavior | Evidence |
|---|---|
| Allow/deny | Production policy through real Hermes `run_one_job`; completed local marker with exact grant, failed attempt without grant. |
| Expiry | Expired durable grant and expired one-time activation consent deny. |
| Replay | Consumed exact approval and a spent occurrence with a new execution ID deny. |
| Revoke | Wrong owner denied; correct revocation denies execution and persists on reopen. |
| Restart | Fresh worker process registers production policy and executes from persisted grant. Abrupt `os._exit` after admission recovers to unknown without replay. |
| Duplicate | Real Hermes duplicate claim denied; simultaneous JL admission has exactly one winner. |
| Mutation | Changed job text or configuration invalidates grant; upstream tests retain broader forbidden-field and immutable-script proof. |
| Global stop | Persisted across reopen; reports in-flight count and rejects later admission. |
| Ledger failure | Query-only ledger denies before script; post-admission write failure retains unknown/replay protection after reopen. |
| Ownership/lifecycle | Second authority owner rejected; missing ledger rejected; inert runtime and repeated shutdown remain inert. |

## Stopped boundary and remaining work

- No scheduler service, worker supervisor, UI, new IPC operation or external
  notification enabled. Only bounded deterministic test execution ran.
- The trusted backend activation API is connected to the existing approval
  store. End-user signed consent presentation/issuance for automation is still
  unexposed; this report does not claim a native consent end-to-end test.
- No autonomous tools, provider/MCP calls, network monitors or live audio/GUI
  actions are part of this integration. The 21 upstream-hook tests still assert
  forbidden-path suppression. No upstream changes or new dependencies this turn.
- Native source is unchanged. Full Python validation preserves Phase 5's
  deterministic behavior; no native build/GUI or live voice proof rerun.
- No power-loss durability or hostile same-user process isolation claim.
- No commit/push or Hermes pin update. Stop here for review as requested.
