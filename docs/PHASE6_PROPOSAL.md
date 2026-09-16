# Phase 6 proposal — Proactive Agent / Automation

Date: 2026-09-15 ICT
Status: scope approved; step 1 feasibility investigated and blocked. See
`PHASE6_FEASIBILITY.md`. Scheduler remains disabled; product implementation has
not proceeded past the feasibility gate.

## Recommendation

Deliver a local scheduled-reminder foundation first. Hermes owns all schedule
parsing, persistence, due selection, claims, execution records, and recovery.
JL owns authenticated management, exact activation consent, allowed job shape,
lifecycle, and the native presentation. No second scheduler.

This is the minimum useful automation slice, not completion of a general
autonomous agent. Tool-using scheduled agents and change monitors are deferred.

## Audit baseline

Read Phase 5 handoff/progress, architecture, security model, and the pinned
Hermes source. Working tree was clean at audit start; submodule was clean at
`044a77b3b6af4ce16138d42762f812a20b9f7a89`.
This is static source evidence, not runtime or GUI validation. No scheduler,
job, provider, microphone, or computer-use operation was started.

All source paths below are relative to `upstream/hermes-agent/`.

| Surface | Evidence and reuse decision |
|---|---|
| Persistent schedules | `cron/jobs.py`: `parse_schedule`, `create_job`, `update_job`, `pause_job`, `resume_job`, `remove_job`. Supports duration/interval, natural every phrases, five-field cron, and ISO one-shots. Reuse storage and time calculation. |
| Built-in scheduler | `cron/scheduler_provider.py`: `InProcessCronScheduler.start`, default 60-second tick, stop event, heartbeat and interrupted recovery. Reuse directly; no JL timer loop, launchd schedule, or external provider. |
| Duplicate/recovery handling | `cron/scheduler.py`: `tick`, `_process_due_job`, `run_one_job`; `cron/jobs.py`: claims and due scan; `cron/executions.py`, `cron/occurrences.py`. File lock, persistent fire claims, in-flight guards, recurring advance before execution, and one-shot dispatch tracking exist. Do not claim exactly-once effects across crashes. |
| No-model execution | `cron/scheduler.py`: `_prepare_job_prompt` short-circuits to `_run_no_agent_job` before importing `AIAgent` or opening its session DB. Requires a script; this is executable code, not a sandbox. Candidate for one fixed JL reminder script only. |
| Script runner | `cron/scheduler_script.py`: `_resolve_script_path`, `_run_job_script`, claim heartbeat. Paths must resolve inside the profile's `scripts/`; runner has timeout/cancellation support. Directory containment does not restrict script effects or inherited credentials. |
| Delivery/history | `cron/scheduler_delivery.py`: explicit `local` target avoids remote delivery; `cron/jobs.py`: `save_job_output`; execution and ledger modules retain results. Reuse local records, project a bounded view into JL. Pin both success and failure delivery to local. |
| Change monitors | `cron/monitor.py`: script/HTTP source, hash/snapshot/diff, unchanged suppression. Snapshot advances before agent success. Network source only checks HTTP(S) scheme, so JL would need destination/data policy. Defer this surface. |
| Proactive suggestions | `cron/suggestions.py`: bounded proposals, dismiss deduplication, accept creates a Hermes job. Reuse later through JL validation/consent; accepting arbitrary stored specs directly is insufficient. |
| Background review | `agent/background_review.py`: model-backed memory/skill review, cancellation and optional tools. Separate from scheduled automation; defer. `gateway/memory_monitor.py` measures process RSS, not user-memory nudges: the older component-matrix description is imprecise. |

## Load-bearing gaps

1. **Default cron is outside JL's execution gate.** `_construct_cron_agent`
   constructs its own `AIAgent`. `_resolve_cron_enabled_toolsets` treats an empty
   list as absent, merges configured MCP into nonempty lists unless `no_mcp` is
   present, and can fall back to default tools on resolution failure. Never
   transfer Phase 5's empty-toolset proof to this path.
2. **Tool filtering is too late for some effects.** Pre-run scripts, monitor
   fetches, dotenv loading, MCP initialization and result delivery need their own
   constraints. Hermes cron approval modes are configurable; they do not supply
   JL's exact trusted consent.
3. **No audited per-job authorization callback.** `can_dispatch` is a tick-wide
   drain gate. `_process_due_job` reclaims the persisted job before execution.
   A creation-time check or pre-tick scan alone does not prove that the executed
   record is still authorized. This is a feasibility gate, not a solved seam.
4. **Ticker housekeeping has scope.** `tick` can run worktree maintenance and MCP
   orphan cleanup even when idle. A dedicated profile and explicit configuration
   must be proven to prevent activity outside the project.
5. **Documentation differs from code.** `cron/AGENTS.md` says `skip_memory=True`;
   `_construct_cron_agent` actually passes `skip_memory=False` and
   `skip_background_review=True`. Source is authoritative at this pin.
6. **Stopping ticks does not prove cancellation.** Queued/in-flight work needs a
   verified drain/cancel contract. A timeout or interrupted record is not proof
   that an effect did not occur; do not automatically replay uncertain attempts.

## Proposed minimal scope

### User-visible behavior

- Automation off by default. User creates a reminder with bounded plain text,
  an explicit one-shot instant or recurring interval, and a visible timezone /
  next-run preview. Hermes parses and calculates the schedule.
- Create paused; exact trusted activation binds owner, job identity/revision,
  text, schedule, local destination, and validity/revocation terms. Editing
  invalidates activation. Persist scoped automation authorization separately
  from ephemeral Phase 3/4 single-action approval tokens.
- Native controls: list/details, create, activate, pause/resume, remove, and
  global stop. Show last result, next run, blocked/missed/interrupted status.
  No run-now button in the first slice.
- Output appears in a bounded in-app activity/history view. No macOS push
  notification permission is required. Reminders run while the foreground JL
  runtime is running; closing the client window is distinct from stopping it.
  No wake-from-sleep or after-logout guarantee; expose Hermes missed-run behavior.

### Integration boundary

- Thin adapter to the pinned built-in Hermes scheduler and job APIs, composed
  by the existing runtime. Native app remains an IPC client.
- Dedicated project-local Hermes home in an isolated worker process to avoid
  global configuration/environment collisions with voice and existing tools.
  Lifecycle control is allowed; scheduling logic remains entirely Hermes-owned.
- Only a fixed reviewed `no_agent` reminder script, with literal data kept out
  of executable code. No user-selected scripts, shell commands, skills, MCP,
  models, context chaining, workdir selection, monitor URLs, or remote delivery.
- JL capability/health projection, strict authenticated IPC, persistent owner
  and authorization checks, metadata-only audit. Reminder text stays in private
  bounded local job/output storage, not in the security ledger.
- Before enabling execution, prove an unmodified supported integration can
  constrain the exact executed job and all ticker side effects. Do not use
  runtime monkeypatching of private scheduler functions. If the pin cannot meet
  this condition, stop at disabled management and report the specific upstream
  extension needed; that is an incomplete Phase 6, not permission to build a
  replacement scheduler or silently widen scope.

## Definition of Done

1. **Reuse:** Hermes pin and checkout unchanged; no duplicate schedule parser,
   job database, due calculator, retry engine, or scheduler loop in JL.
2. **Integration feasibility first:** real pinned imports in a temporary home
   inside the project prove profile isolation, fixed-script dispatch, exact
   authorization enforcement, local-only output, and harmless housekeeping.
   Zero model/provider/MCP calls and zero writes outside the project.
3. **Functional path:** authenticated create -> paused -> trusted activate ->
   real Hermes due selection/claim -> fixed reminder -> visible local result;
   recurring and one-shot paths both covered. Pause, edit, revoke, remove and
   restart behavior are explicit and tested.
4. **Adversarial proof:** reject unknown fields, forged/cross-owner requests,
   changed job/script/config after authorization, path/symlink escapes, arbitrary
   scripts, hidden delivery targets, model/tool/MCP enablement, expired/revoked
   grants, and store corruption. Race activation/revocation against queued work;
   no unauthorized dispatch after the defined revocation boundary.
5. **Lifecycle proof:** duplicate runtime/ticks do not duplicate a scheduled
   occurrence; interrupted/ambiguous attempts are surfaced without automatic
   replay. Verify missed one-shots, sleep/downtime, timezone interpretation,
   bounded shutdown and no orphan worker. Do not promise exactly-once delivery.
6. **Native proof:** strict IPC/native contract tests plus a bounded local GUI
   reminder smoke. Build success alone is not GUI evidence. Keep Phase 5 voice
   disabled in these tests and preserve its tool-free contract.
7. **Validation/closeout:** targeted integration/adversarial tests, existing JL
   Python/native checks, lint/type/dependency/build checks, security/architecture
   updates and Phase 6 report/handoff. Final approved implementation closeout
   verifies clean tree/submodule, exact pin, commit/push and HEAD = origin/main.

## Implementation order after scope acceptance

1. Prove the supported constrained execution seam and isolation offline.
2. Add the narrow authorization/adapter contracts and adversarial tests.
3. Wire runtime lifecycle, registry/health, and authenticated IPC.
4. Add native reminder controls and local results.
5. Run the bounded integration/GUI proof and document closeout.

Deferred: general scheduled LLM turns, autonomous tool/GUI actions, outbound
messages, arbitrary scripts, network monitors, suggestion generation, memory
review, voice-triggered automation, OS notification delivery, and always-on
production hosting. Each needs its own explicit scope and security proof.
