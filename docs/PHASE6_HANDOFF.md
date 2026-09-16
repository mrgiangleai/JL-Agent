# Phase 6 handoff — Proactive Agent / Automation

Date: 2026-09-16 ICT

## Status

Phase 6 is complete for the approved minimal scope. Stop here unless the user
explicitly starts Phase 7.

Do not rerun the accepted live scheduler smoke unless a failed validation
requires a new, bounded diagnostic and the user explicitly approves it.

## Read first

1. `docs/PHASE6_REPORT.md`
2. `docs/PHASE6_UPSTREAM.patch`
3. `docs/PHASE6_SCHEDULER_LIVE_VALIDATION.md`
4. `docs/PHASE6_NATIVE_UI_VALIDATION.md`
5. `docs/PHASE6_AUTHORIZATION_VALIDATION.md`
6. `docs/PHASE6_HOOK_VALIDATION.md`
7. `docs/SECURITY_MODEL.md`
8. `docs/ARCHITECTURE.md`
9. `docs/PROGRESS.md`

## Critical Hermes note

Hermes is still pinned at:

`044a77b3b6af4ce16138d42762f812a20b9f7a89`

The Hermes worktree intentionally contains a reviewed three-file patch:

- `cron/execution_policy.py`
- `cron/scheduler.py`
- `cron/scheduler_script.py`

This is not accidental dirt. It is the load-bearing JL per-job authorization
hook for Phase 6. `docs/PHASE6_UPSTREAM.patch` is the archived patch artifact,
and `src/jl_agent/automation_runtime.py` verifies these patched file hashes at
worker bind time.

When upgrading Hermes, this patch is the first thing to re-audit and rebase
before changing the pin. Do not update the submodule revision, regenerate
hashes, or enable automation on a new Hermes version until the hook contract and
adversarial tests pass again.

Current required patched-file SHA-256 values:

```text
00aee1e4471060ba0602bc8cfeabb22d01e59c1b840b68a537b2730316c7e8aa  cron/execution_policy.py
4623c40ba022f26cc56a54e8a050c0487979c646ddb2bc2db2cab72f6d36bd94  cron/scheduler.py
84b6a68287614d793b8e78d949f0db19f74e9bd2a55ddfb3bec995c03722ec06  cron/scheduler_script.py
```

## Contracts to preserve

- Hermes owns scheduling, job storage, due selection, claims, output and
  execution history.
- JL owns exact native consent, durable grants, replay receipts, revoke/stop
  policy and final admission.
- Jobs are fixed local `no_agent` reminders only.
- Jobs are created paused and must receive exact signed activation before
  execution.
- The automation service is explicit foreground lifecycle only. There is no
  supervisor, LaunchAgent, automatic retry or global resume.
- No provider, MCP, browser, network monitor, external messaging, autonomous
  tool or arbitrary script path may be added under Phase 6.
- Runtime state belongs on internal APFS. Do not use the exFAT project volume
  for SQLite runtime state.

## Deterministic validation

Use these checks before touching Phase 6 code:

```bash
.venv/bin/python -B -m unittest
.venv/bin/python -m ruff check src tests scripts/phase6-live-smoke.py
.venv/bin/python -m ty check src tests
.venv/bin/python -m pip check
cd macos-app && swift run JLAgentNativeTests && swift build
cd ..
macos-app/Scripts/build-app.sh
git diff --check
git -C upstream/hermes-agent apply --reverse --check ../../docs/PHASE6_UPSTREAM.patch
shasum -a 256 upstream/hermes-agent/cron/execution_policy.py \
  upstream/hermes-agent/cron/scheduler.py \
  upstream/hermes-agent/cron/scheduler_script.py
```

AF_UNIX runtime tests may need a normal unsandboxed local process on macOS.
That is a test-environment constraint, not permission to weaken the IPC
transport.

## Accepted live proof

One live local scheduler job passed on internal APFS:

- Job `3b43dfc78d98`
- Execution `3141cb5bdd95492fa9b07f48ca94a0c0`
- One script launch, one completed history row, one output marker, one spent
  JL receipt.

Evidence is preserved under:

`~/Library/Application Support/JL Agent/runtime/phase6-live-smoke-20260915`

Do not reuse that home or rerun the live smoke casually.

## Deferred work

- Phase 7 product/workflow scope is undefined until the user provides a new
  brief.
- Production distribution signing and any supervisor/LaunchAgent story are
  deferred.
- Notifications, external messaging, provider/MCP jobs, browser/network
  monitors, file actions and autonomous agent jobs are out of Phase 6 scope.
- Global stop has no approved resume operation.
- Power-loss durability beyond deterministic replay/restart tests is not
  claimed.
- The GUI smoke validated rendering/control surface only. Backend/native
  contract tests cover activation; the GUI was not used to run a live job.

## Next boundary

Phase 7 can start only after an explicit user brief. The first action in any
Hermes upgrade or Phase 7 automation expansion is to re-audit/rebase the
intentional Hermes three-file patch.
