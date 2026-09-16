# Phase 6 final report — Proactive Agent / Automation

Date: 2026-09-16 ICT

## Outcome

Phase 6 is complete for the approved minimal automation scope. JL now exposes
local reminder scheduling through Hermes' existing cron system without creating
a second scheduler.

The final Phase 6 surface is intentionally narrow:

- fixed local `no_agent` reminder jobs only;
- jobs are created paused;
- activation requires exact signed native consent;
- Hermes owns schedules, claims, due selection, execution, output and history;
- JL owns grants, replay receipts, revoke/stop policy and native consent;
- no provider, MCP, network monitor, external messaging, autonomous tool path,
  global resume, scheduler supervisor or automatic retry was added.

## Implemented boundary

- `src/jl_agent/control/automation.py` implements the production authority:
  exact grant binding, durable SQLite receipts, replay denial, expiry, revoke,
  global stop and ledger-failure fail-closed behavior.
- `src/jl_agent/automation_runtime.py` registers the production policy with the
  approved Hermes hook only inside an explicitly prepared worker. It verifies
  Hermes pin and hook file SHA-256 before binding.
- `src/jl_agent/automation_service.py` is an explicit foreground lifecycle
  adapter for Hermes' `InProcessCronScheduler`. It verifies internal APFS,
  delegates ticks to Hermes, stops on faults and persists global stop on
  shutdown.
- `src/jl_agent/control/automation_management.py` provides authenticated native
  management IPC around Hermes job/history APIs. It scopes every operation to
  the automation profile and never owns scheduling.
- The SwiftUI app exposes schedules list, create paused one-time/recurring
  reminder, exact activation sheet, pause, remove, history/status, Stop All and
  disabled/stopped status text.

## Hermes patch

Hermes remains pinned at:

`044a77b3b6af4ce16138d42762f812a20b9f7a89`

The working Hermes checkout intentionally carries a reviewed three-file patch:

| File | Purpose | SHA-256 |
|---|---|---|
| `cron/execution_policy.py` | New opt-in execution policy registration, request/lease contract, strict profile/job validation, denial recording and fault latch. | `00aee1e4471060ba0602bc8cfeabb22d01e59c1b840b68a537b2730316c7e8aa` |
| `cron/scheduler.py` | Requires policy authorization for embedded JL jobs, disables detached worker bypass, keeps local output only and suppresses unrelated housekeeping in required-policy mode. | `4623c40ba022f26cc56a54e8a050c0487979c646ddb2bc2db2cab72f6d36bd94` |
| `cron/scheduler_script.py` | Runs approved immutable script bytes through isolated stdin instead of reopening mutable script paths. | `84b6a68287614d793b8e78d949f0db19f74e9bd2a55ddfb3bec995c03722ec06` |

The patch is archived in `docs/PHASE6_UPSTREAM.patch` and currently
reverse-applies cleanly against the patched Hermes worktree. This is deliberate
local upstream debt. The first task for any future Hermes upgrade is to
re-audit, rebase and revalidate this exact hook before changing the pin or
enabling automation.

## Live proof

Exactly one authorized local scheduler smoke passed on internal APFS:

- Job: `3b43dfc78d98`
- Execution: `3141cb5bdd95492fa9b07f48ca94a0c0`
- Scheduled occurrence: `2026-09-15T15:27:17.772945+00:00`
- Result: one completed execution, one local output file, one consumed exact
  approval, one spent JL receipt, no duplicate over the observation window.

The fixed script bytes were:

```python
print("JL_REMINDER_DONE")
```

No provider, MCP, network monitor, external messaging, autonomous tool, voice,
or GUI automation action ran in the live smoke. It was not retried.

Preserved evidence directory:

`~/Library/Application Support/JL Agent/runtime/phase6-live-smoke-20260915`

## Validation

Final closeout checks passed:

```text
.venv/bin/python -B -m unittest                 177 tests PASS
swift run JLAgentNativeTests                    18 native tests PASS
swift build                                     PASS
macos-app/Scripts/build-app.sh                  PASS
.venv/bin/python -m ruff check src tests ...    PASS
.venv/bin/python -m ty check src tests          PASS
.venv/bin/python -m pip check                   PASS
git diff --check                                PASS
git -C upstream/hermes-agent apply --reverse --check docs/PHASE6_UPSTREAM.patch PASS
```

The SQLite 3.45.3 WAL safety warning appeared during Hermes execution tests;
Hermes used DELETE journaling for the affected ledger path. The separate
readonly SQLite diagnosis remains documented in `PHASE6_SQLITE_DIAGNOSIS.md`.

## Remaining limitations and deferred work

- The Hermes hook is a maintainable but real upstream patch. It must be the
  first re-audit/rebase item during any Hermes version upgrade.
- Phase 6 supports local reminder scripts only. There is no email, chat,
  provider, MCP, network monitor, browser action, file action or autonomous
  agent job.
- Global Stop All is terminal for the current runtime profile. There is no
  global resume; individual jobs require a new exact activation after restart
  or after stop state is intentionally reset by future approved work.
- No power-loss durability guarantee is claimed beyond the deterministic
  restart/replay tests and one live service smoke.
- The boundary does not sandbox hostile same-user code. It is an application
  authorization boundary for reviewed Hermes scheduler execution.
- The GUI smoke verified rendering and controls only. It did not activate a
  live schedule through the GUI; backend and native contracts cover that path.
- Distribution signing, LaunchAgent/supervisor behavior, notification delivery,
  external messaging and Phase 7 features are deferred.

## Phase boundary

Phase 6 stops here. Phase 7 requires a new explicit brief. Do not widen
automation beyond fixed local reminders without re-auditing the Hermes hook and
the JL authorization contract.
