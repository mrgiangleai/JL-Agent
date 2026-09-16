# Phase 6 hook — deterministic validation checkpoint

Date: 2026-09-15. Stop point requested by the user: review upstream diff before
enabling scheduler or UI. This is not Phase 6 completion or production readiness.

## APFS prerequisite: PASS before upstream edits

`scripts/verify-phase6-apfs.py` used a private, disposable JL runtime home under
`/private/tmp/jl-phase6-runtime-*` on internal APFS (`/dev/disk3s5`). This location
was authorized by the user's internal-APFS probe instruction. Only the probe's
own directory was removed. The production JL runtime location was not changed.

Real pinned Hermes APIs, without patches/mocks, passed:

- Create paused; refuse claim while paused.
- Claim exclusion from a second process while the owner is alive.
- Claimed -> running -> completed and terminal-state immutability.
- Completed occurrence persisted and found after interpreter restart.
- Abrupt owner exit via `os._exit(0)` after durable claimed/running writes;
  fresh interpreter recovers both attempts to `unknown`.
- A second fresh interpreter finds recovery idempotent, retains the completed
  occurrence and paused job, and creates no retry attempts.
- SQLite `integrity_check=ok`, durable `journal_mode=delete`.

No DBMOVED/readonly failure occurred. Same Python/SQLite 3.45.3 as the exFAT
failure, unchanged Hermes ledger code. The existing SQLite-version warning and
DELETE fallback remain; no durability setting was weakened or dependency
upgraded. This proves process-restart behavior, not power-loss durability.

## Upstream patch footprint

Base HEAD remains `044a77b3b6af4ce16138d42762f812a20b9f7a89`.
Exactly three upstream files changed locally:

| File | Diff |
|---|---|
| `cron/execution_policy.py` | New, 274 lines: profile policy registration, immutable request/lease, strict configuration/job validation, admission/replay checks, denial recording and fault latch. |
| `cron/scheduler.py` | +33 / -14: shared execution gate, direct-call and detached-worker denial, local-output-only branch, skip secret hydration and non-job housekeeping for required profiles. |
| `cron/scheduler_script.py` | +23 / -11: execute approved immutable UTF-8 script via isolated interpreter stdin, minimal environment, existing cancellation/timeout cleanup. |

Total: **330 insertions, 25 deletions**. Full review artifact:
`docs/PHASE6_UPSTREAM.patch` (includes the new untracked module).
Contract tests live in JL `tests/test_phase6_hook.py`; no fourth upstream file
was edited. No scheduler parser/store/provider/ledger implementation was copied.

## Effective contract

- Required profile declares `cron.execution_policy: jl`; trusted embedding code
  calls `register_execution_policy(home, "jl", policy)` before using Hermes.
  The host supplies `acquire(request)` as a bounded context manager returning
  a matching, unexpired `ExecutionLease` containing audited immutable script bytes.
- Missing registration, malformed/duplicate config, config changes after
  registration, job drift, unknown/forbidden fields, cancellation, wrong/replayed
  attempts and invalid leases deny. Policy cannot be replaced in-process.
- Only `no_agent`, explicit local success/failure delivery and no extra prompt
  are admitted. Immutable script bytes avoid reopening a replaced path. The
  child receives a minimal environment and a ten-second execution timeout.
- `run_one_job` holds the lease through Hermes execution/bookkeeping.
  Direct `run_job` and script calls lack the active lease and deny. Required
  detached-worker entry denies before payload adoption. Registered profiles
  skip the general remote-delivery pipeline and idle worktree/MCP cleanup.
- Denied claimed jobs pause without consuming repeat count; local attempt
  failure is recorded with a constant reason. Ledger exceptions and failed
  shared execution latch the registered profile closed until worker restart.
- Existing unconfigured profiles retain their legacy behavior. This is opt-in
  authorization for JL's profile, not a global policy imposed on other users.

The production JL policy implementation, persistent consent store, revocation
UI and worker lifecycle wiring are deliberately not added in this checkpoint.
The tests register a deterministic host policy. Future JL code must serialize
grant/revoke admission and never auto-replay unknown attempts. The hook does not
provide an OS sandbox or protect against arbitrary same-user code.

## Validation

Final commands/results:

```text
.venv/bin/python -B scripts/verify-phase6-apfs.py       PASS (before patch)
.venv/bin/python -B -m unittest discover -s tests      147 tests PASS
.venv/bin/ruff check src tests scripts/verify-phase6-apfs.py
    upstream/hermes-agent/cron/execution_policy.py     PASS
.venv/bin/ty check                                   PASS
.venv/bin/python -m pip check                        PASS
git diff --check                                    PASS
git -C upstream/hermes-agent diff --check            PASS
```

The full suite contains the prior 126 JL tests plus 21 hook tests. It required
normal local execution: the sandbox initially blocked nine existing AF_UNIX
bind tests with EPERM; they passed on the authorized outside-sandbox rerun.

Hook coverage includes real local script execution, real due one-shot tick and
duplicate tick, provider execution, direct-call denial, expired/revoked leases,
pause/edit during authorization, script replacement, forbidden shapes, config
downgrade/duplicate keys, absent policy in another process, registration
replacement, ledger fault latch, cancellation, isolated environment, and
legacy-profile compatibility.

Test guards assert no model/MCP/dotenv imports, secret/terminal scope hydration,
network connects, external delivery/launch, or housekeeping in required-policy
runs. An intermediate adversarial fixture incorrectly called upstream job
management with `no_agent=False` before testing the gate; in the full suite it
attempted metadata-service discovery, which the network guard blocked. The
fixture now supplies hostile input directly to the execution gate. The final
suite reports zero guarded calls. Future JL management must validate before
calling Hermes create/update; this checkpoint does not enable those APIs.

## Stopped state

- Only bounded test ticks ran; no scheduler service was started/enabled.
- No UI added or enabled. No native GUI validation claimed.
- Phase 5 product source/configuration unchanged; its deterministic Python
  tests passed in the full suite. No live audio or computer-use operation ran.
- No provider/MCP dependencies installed, no external notification sent.
- No commit, push, submodule pin update or upstream publication performed.
- APFS production state placement and full JL activation/consent integration
  remain separate work; review this diff first as requested.
