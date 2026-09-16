# Phase 6 — feasibility gate blocked

Date: 2026-09-15 ICT

The user approved `PHASE6_PROPOSAL.md`. Work proceeded sequentially into step 1.
Phase 6 is incomplete. No runtime, IPC, consent, or native automation UI has
been added; no scheduler or reminder script was executed.

## Primary blocker: supported per-job authorization seam

At the unchanged Hermes pin
`044a77b3b6af4ce16138d42762f812a20b9f7a89`:

- `cron/scheduler_provider.py:InProcessCronScheduler.start` calls the module's
  `tick`; subclassing `fire_claimed` does not intercept this built-in path.
- `cron/scheduler.py:tick` checks zero-argument `can_dispatch` before selecting
  due jobs, then submits workers asynchronously.
- `_process_due_job` claims a fresh persisted record and calls `run_one_job`
  directly. No JL authorization callback is passed or invoked.
- `run_one_job` can launch a detached worker before `_run_one_job_body` enters
  script/agent execution. The shared body installs secret/terminal scopes and
  executes `run_job`. Existing ownership fences prove Hermes claim ownership,
  not JL's approved job revision, script identity, or authorization validity.
- `_prepare_job_prompt` enters `_run_no_agent_job` directly. That path loads
  dotenv and executes the job's script. Agent tool middleware is not a gate for
  this path.

A pre-tick store scan cannot bind the later claimed record. A check inside the
approved script cannot stop a substituted script from running instead. Pause
blocks new claims but is not proof of revoking an already claimed snapshot.
The source was also searched for cron policy/hooks/middleware/callbacks; no
supported per-job integration was found on this execution path.

These findings concern the required JL contract, not a claim that Hermes fails
its own documented scheduling contract. No private-function monkeypatch,
replacement scheduler, or upstream edit was introduced.

## Reproducible offline probe

```bash
.venv/bin/python -B scripts/verify-phase6-feasibility.py
.venv/bin/ruff check scripts/verify-phase6-feasibility.py
git diff --check
```

The probe imports real `cron.jobs` and `cron.scheduler_provider` from the pin,
clears inherited environment variables, uses a temporary Hermes home under
project `artifacts/`, and removes only its own temporary directory. It never
starts a ticker, invokes a script, imports the full scheduler for execution,
or dispatches a provider/MCP/delivery operation. It reads the scheduler source
with AST to report the actual built-in dispatch calls. No upstream mocking.

Observed results:

| Check | Result |
|---|---|
| Dedicated project-local job store | PASS |
| Created paused; paused claim refused | PASS |
| Resume and first claim | PASS |
| Second live claim refused | PASS |
| Pause prevents a new claim | PASS |
| Reload store retains pause | PASS |
| Previously claimed snapshot remains scheduled | CONFIRMED; not an execution test |
| Completed-occurrence lookup | DEGRADED: `sqlite3.OperationalError: attempt to write a readonly database` during execution-ledger schema initialization |
| Ruff and diff whitespace checks | PASS |
| Hermes pin and submodule cleanliness | PASS |

The SQLite lookup exception is caught by Hermes `completed_occurrence`, which
returns false. The probe therefore reports it separately and must not be used
as evidence of durable occurrence deduplication. Root cause is not established;
do not assume an upstream regression or change SQLite/dependencies from this
result. Hermes also reports its linked SQLite 3.45.3 and selects DELETE journal
mode through its existing fallback. No environment upgrade was attempted.

Store reload is not worker restart. Actual scheduled execution, completed-effect
duplicate prevention, crash recovery, in-flight revocation, housekeeping
isolation, and native GUI behavior remain unvalidated. No full Phase 5 test
suite was rerun because its source/configuration was not changed.

## Required next decision (outside this approved implementation scope)

Review an additive upstream integration contract before changing the pin:

1. The built-in execution path must accept a runtime-owned per-job gate after
   claiming the exact snapshot and before any script, model, monitor, external
   worker launch, or delivery effect. Missing/failed JL authorization denies.
2. Bind the gate to job revision, approved script identity, owner, validity,
   local delivery and a revocation generation. Define the point after which
   an in-flight effect has started and cannot be represented as cancelled.
3. Preserve that authority through every supported execution lane, or provide
   a supported way to disable detached/manual/external lanes for this profile.
4. Preserve Hermes claims, ledger, lifecycle and scheduling. Denials must be
   recorded without calling failure delivery outside the approved local scope.
5. Expose/prove configuration that contains idle housekeeping to this profile.

This is a proposed contract to review, not an upstream patch or permission to
upgrade. First resolve that decision and the local SQLite probe limitation;
then repeat step 1 before continuing authorization, IPC, and UI work.

No implementation closeout commit/push or Phase 6 completion is claimed.
