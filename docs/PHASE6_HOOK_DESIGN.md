# Proposed Hermes execution-authorization patch

Date: 2026-09-15. DESIGN ONLY — approval required before any upstream change.
Base: `044a77b3b6af4ce16138d42762f812a20b9f7a89`.

## Decision

Use one profile-scoped, process-local execution policy, consulted at the shared
`run_one_job` boundary. Do not thread a callback through every ticker/provider
method and do not replace `InProcessCronScheduler`. An enabled policy owns a
bounded execution lease; a boolean approval callback alone is insufficient for
revocation and script-substitution races.

Keep the first policy-enabled mode deliberately limited to in-process,
`no_agent`, local-output jobs. Other Hermes profiles retain existing behavior.
No automatic import of policy code named in a job/config; trusted embedding code
registers it before starting Hermes. JL's worker hosts exactly one profile.

## Exact proposed production changes

Paths are relative to `upstream/hermes-agent/`. No edits made.

| File / functions | Proposed change |
|---|---|
| NEW `cron/execution_policy.py` | Define request/lease protocol, register a policy once against a resolved profile home, strict policy resolution, and private active-lease context. Read optional `cron.execution_policy` as a policy ID, not a Python import path. Installed policy remains required even if config is subsequently removed or changed. |
| `cron/scheduler.py:tick` | Resolve policy before housekeeping or due selection. Missing required implementation/config errors stop dispatch. Policy-enabled embedded profile skips worktree maintenance and MCP orphan sweeping; retain scheduling locks, claims, due calculation, recovery and heartbeat. Carry the skip decision to both synchronous cleanup and `_sweep_mcp_orphans_when_all_done` callback registration. |
| `cron/scheduler.py:run_one_job` | Resolve policy, normalize extra prompt before authorization, copy the claimed snapshot, create/reuse attempt metadata, acquire lease before `_launch_external_cron_worker` or execution bookkeeping that consumes a finite dispatch. Required-policy runs never enter detached launch. Hold lease through execution and local result bookkeeping; release in `finally`. Denial uses a dedicated local terminal path, not `_deliver_crash_failure`. |
| `cron/scheduler.py:run_job` | Required policy demands a matching active lease (profile, execution, exact snapshot), otherwise deny before `_prepare_job_prompt`. This closes direct low-level calls. Do not independently re-authorize a second time. |
| `cron/scheduler.py:_run_no_agent_job` | Required-policy mode uses the lease's approved script bytes and skips dotenv hydration. Normal mode unchanged. |
| `cron/scheduler.py:_run_one_job_body` | In required-policy mode skip secret and terminal scope initialization (no model/tools/remote delivery allowed); preserve claims, heartbeat, cancellation and ledger logic. Guard exceptional delivery so a policy failure never enters remote failure routing. |
| `cron/scheduler_script.py:_run_job_script` | For an active matching lease, run its bounded immutable script bytes with the current interpreter using `-I -`, supplied once to stdin; never reopen the mutable script path. Retain existing timeout/process cleanup/redaction; use a minimal approved environment and profile-local cwd. Normal path-based script behavior unchanged. |

No changes to `cron/jobs.py`, schedule parsing, provider interfaces, or ledger
schema are proposed. `InProcessCronScheduler.start`, `_process_due_job`,
`CronScheduler.fire_claimed`, and external payload adoption already converge on
`run_one_job`. Tests must prove this rather than assume it after an update.

Add `tests/cron/test_execution_policy.py` and update
`website/docs/developer-guide/cron-internals.md` / `cron/AGENTS.md` to document
the contract. Do not copy the existing inaccurate memory flag description.

## Hook contract (API sketch, not implemented code)

```python
register_execution_policy(profile_home, policy_id, policy)

class ExecutionPolicy(Protocol):
    def acquire(self, request: ExecutionRequest) -> ContextManager[ExecutionLease]: ...
```

`ExecutionRequest` contains canonical profile home, job ID, execution ID,
scheduled occurrence, fire owner, the complete copied JSON job snapshot, and
effective extra prompt. Give the hook a detached read-only representation;
mutating callback input cannot change the private snapshot Hermes executes.
Attempt fields such as claim timestamps are not part of the user's semantic
approval fingerprint; JL explicitly separates runtime metadata from every
effect-bearing field. Unknown semantic fields fail closed.

`ExecutionLease` contains the bound identity/fingerprint and bounded immutable
approved script bytes. JL verifies its own shipped script digest, then returns
those exact bytes; no job text becomes executable code. A reminder script may
emit a fixed completion marker and JL maps the job ID to approved reminder text.
Script imports, `__file__`, arguments, stdin, and working-directory assumptions
must be audited for this execution mode; only the fixed Phase 6 script uses it.

The policy-enabled first version structurally requires `no_agent=true`, explicit
success AND failure destination `local`, no origin, skills, model/provider,
monitor, context chaining, custom workdir, toolset, or extra prompt. JL also
validates configured environment and exact approved data. The required-policy
path must not hydrate secrets or inherit provider/MCP credentials. The script
runner is an execution adapter, not a tool dispatcher.

### Required vs optional

- Existing profile with no configured or installed policy: legacy behavior.
- Configured policy ID with no matching registered policy: deny. This includes
  another CLI/process trying to execute this profile without JL registration.
- Once JL registers a required policy, config removal/change cannot downgrade
  that worker to legacy mode. Missing/malformed config, unknown policy ID,
  profile mismatch, missing lease or forbidden fields deny.
- Registry replacement/unregistration while active is forbidden. Stop the
  worker for explicit reconfiguration. Registry lookup is thread-safe and
  scoped by canonical home, not a process-global yes/no flag.
- Missing/false/malformed lease, exception, expired/revoked grant, changed
  script/job/config, unavailable JL authority or cancellation: no execution.
- Callback must be bounded: JL uses a bounded authenticated local authority
  call, never a provider. Reject an already-expired decision at return. A
  Python hook cannot forcibly stop arbitrary hung trusted code; if it hangs,
  no effect has begun and the owner stops the isolated worker. Do not add a
  generic executor that can later accept a timed-out authorization response.

### Denial and ledger failure

Use existing failed attempt state with an allowlisted reason such as
`authorization_denied`, plus owner-fenced `mark_job_run`/claim cleanup as
appropriate. No new public terminal state/schema migration. Do not consume a
one-shot dispatch on denial. Do not generate a delivery payload or call failure
delivery. JL pauses/revokes unauthorized jobs to avoid repeated denied attempts;
Hermes still owns all schedule state and transitions.

If attempt recording fails, execution does not start. If denial bookkeeping
fails, stop dispatch and surface the local error. Do not convert database
failure to permission, and do not retry an uncertain effect.

## Revocation and immutable execution

JL serializes acquire/activate/edit/revoke/global-stop on its existing authority
boundary. Acquire atomically checks the persisted exact grant, validity window,
revision and revocation generation, then marks this occurrence in-flight. The
lease is retained until the bounded script and local bookkeeping finish.

Revocation winning before acquisition denies that attempt. Revocation after
acquisition prevents all new leases, reports the existing occurrence as
in-flight, and waits for bounded drain or records interruption. It must not
claim to undo a completed reminder. Global stop first closes admission, then
signals Hermes lifecycle shutdown and drains the isolated worker.

The lease is not a reusable approval token. Restart requires fresh policy
registration and revalidation of the persistent grant; Hermes recovers its
attempt records. An unknown attempt is never automatically replayed by JL.

Executing immutable approved bytes is necessary: checking a path hash and then
letting Hermes reopen that path would still permit substitution between check
and use. No file descriptor or lease is serialized into a job record. Detached
execution is excluded in this first version because transporting authority
safely would expand the patch.

Threat limit: this is an application authorization boundary, not containment
against malicious code already running as the same OS user. Such code could
edit the profile opt-in and run another interpreter or bypass Hermes entirely.
Do not claim this patch solves OS isolation, file permissions on exFAT, or TCC.

## How JL integrates

1. Start one isolated project-local worker with a minimal environment and fixed
   profile. Verify pinned patch identity and profile config before readiness.
2. Register the JL policy once, then use Hermes `InProcessCronScheduler.start`.
3. Native authenticated IPC creates paused Hermes records; trusted exact
   consent creates JL's durable narrow grant and resumes through Hermes APIs.
4. Hermes selects/claims occurrences; the shared execution hook asks JL for
   authority. JL never calculates due times or runs a polling scheduler.
5. Hermes records local output/history; JL projects a bounded native view.

## Acceptance tests before enabling

- Legacy profile behavior unchanged; required profile without hook denies.
- Built-in tick, provider/manual path, direct `run_job`, and external payload
  entry cannot bypass required authority; no detached launch occurs.
- Exact snapshot reaches hook; callback mutation, persisted edits, unknown
  fields, changed script and changed delivery cannot expand effects.
- Replace the script path after lease acquisition: only approved immutable
  bytes can execute. Assert no dotenv/provider/MCP/monitor/remote delivery path.
- Deterministically order revoke before/after lease acquisition and queued
  work; validate global stop and crash/restart without replaying unknown work.
- Missing/failed ledger prevents start; denied attempts never send alerts.
- Policy-enabled idle ticks perform no worktree maintenance or MCP cleanup.
- Real one-shot and recurring script execution, occurrence deduplication,
  profile isolation and bounded shutdown; mocks alone are insufficient.

## Maintainability assessment

Small and localized, but **not safely a one-callback, ten-line patch**. The
minimal complete design affects three production files (one new) plus tests
and docs. Estimate a few hundred lines, not a promised diff size. Most logic
stays in the new module; the scheduler contains short integration branches.

Scheduler refactors, alternate execution lanes, script spawning and failure
delivery are the update-sensitive areas. Carry as one separate commit against
the exact pin, require the above contract tests on every update, and review
new call sites. Prefer upstream acceptance. Never automatically resolve patch
conflicts or silently omit a gate. If implementation requires a new scheduling
engine, generalized sandbox, or cross-process authority transport, stop again.

Approval sought for this design only; no upstream modification is authorized
by the present request. SQLite diagnosis is separate in `PHASE6_SQLITE_DIAGNOSIS.md`.
