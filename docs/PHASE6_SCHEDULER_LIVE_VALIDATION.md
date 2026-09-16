# Phase 6 — scheduler service / one local live smoke

Date: 2026-09-15. **PASS. Stopped before UI implementation.**

## Implemented boundary

- `src/jl_agent/automation_service.py` is an explicit foreground worker. It calls
  Hermes `InProcessCronScheduler.start`; Hermes owns recovery, ticks, due
  selection, claims, occurrence records and execution history. No second timer
  or scheduler, supervisor, launch agent or automatic restart was added.
- Worker startup verifies the backing device is internal APFS and binds the
  existing production JL hook. The Hermes pin and three-file patch are unchanged.
- The dispatch gate checks policy presence, durable global stop and ticker errors.
  The existing per-job hook remains mandatory and consumes an occurrence receipt
  before supplying the fixed audited script. Service startup cannot issue grants.
- Shutdown closes admission, drains bounded in-flight execution, persists global
  stop and closes the authority. Ledger failure stops the worker without retry.
- Main JL runtime composition stays inert. No UI or automation IPC was added.
  Explicit standalone entry point: `python -m jl_agent.automation_service --home
  <prepared-internal-APFS-profile>`. It does not create consent or reset global stop.
- `scripts/phase6-live-smoke.py` is a single-use operator validation harness. It
  refuses an existing home, records its attempt before creating a job and retains
  evidence. The user's explicit smoke authorization supplies the exact consent
  for this fixed job; this is not a native signed-consent UI end-to-end test.

## Exactly one live attempt

Runtime home (preserved):

`/Users/keinle/Library/Application Support/JL Agent/runtime/phase6-live-smoke-20260915`

Backing device: `/dev/disk3s5`, internal APFS on `/System/Volumes/Data`.
Private authority ledger uses DELETE journaling/FULL synchronization. Hermes
also uses DELETE journaling; its installed SQLite 3.45.3 emitted its existing
WAL safety warning. No dependency or pin update was performed.

The job was created paused, exact approval consumed, then explicitly resumed.
Grant lifetime was 180 seconds. The only script bytes were
`print("JL_REMINDER_DONE")\n`, supplied through the production JL execution lease.
No manually forced firing was used: real Hermes service ticks selected the job.

| Check | Observed evidence |
|---|---|
| Job | `3b43dfc78d98`, one-shot, `no_agent`, local delivery |
| Scheduled occurrence | `2026-09-15T15:27:17.772945+00:00` |
| Execution | `3141cb5bdd95492fa9b07f48ca94a0c0`, source `builtin` |
| History | Exactly one row, `completed`, no error; delivery `suppressed` |
| Execution time | Started `15:27:18.159584Z`, finished `15:27:18.198096Z` |
| Single execution | Python subprocess audit observed exactly one fixed-script launch |
| Local output | Exactly one Markdown file with one `JL_REMINDER_DONE` marker |
| Authorization | Exact activation approval `CONSUMED`; one matching receipt `spent` |
| Occurrence | Hermes completed-occurrence lookup returned true |
| Duplicate observation | Three more seconds of real ticks after completion; history remained one row |
| Prohibited paths | Zero network audit events or forbidden agent/MCP/env-loader imports |
| Final state | Service exited successfully, `scheduler_enabled=false`, global stop persisted |

`attempt.json`, `authorization-evidence.json`, `result.json`, Hermes databases,
output history and JL authorization database remain in that runtime home. No
approval secret is written in the evidence. No automatic retry occurred.
The smoke observer only reads history and signals stop; it never fires a job.

## Deterministic validation

- Full Python suite: **174 tests PASS** (prior 168 plus six service/APFS tests).
- Service tests cover upstream lifecycle delegation, no approval issuance,
  persisted stop, repeated start rejection, missing hook, ticker/ledger faults,
  drain/close after stop-write failure and internal APFS enforcement.
- Existing tests retain allow/deny, expiry, replay, revoke, restart, duplicate
  claim, mutation, global stop, ledger failures and Phase 5 regressions.
- Ruff, ty, dependency consistency and Git whitespace checks pass.
- The reviewed Hermes patch remains byte-for-byte equal to
  `docs/PHASE6_UPSTREAM.patch`; HEAD stays
  `044a77b3b6af4ce16138d42762f812a20b9f7a89`.

## Stopped boundary

The service was enabled only for this bounded authorized live run and is now
stopped. No unattended worker remains intentionally enabled, no retry or global
resume was performed, and no UI implementation started. This is one real local
execution plus deterministic regression evidence, not power-loss or GUI proof.
No provider, MCP, network, external messaging, autonomous tool, microphone or
native-app action was performed. No new upstream edits, commit or push.
