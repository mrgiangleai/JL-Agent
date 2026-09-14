# Phase 4C complete handoff

## Read this first

Phase 4C is complete. Do not repeat its live computer-use smoke, change TCC,
or begin Phase 5 without a new explicit brief.

Read in this order:

1. `docs/PHASE4C_REPORT.md`
2. `docs/PHASE4C_HOST_SETUP.md`
3. `docs/PHASE4B_HERMES_AUDIT.md`
4. `docs/ARCHITECTURE.md`
5. `docs/SECURITY_MODEL.md`
6. `docs/PROGRESS.md`

## Completed boundary

- Hermes remains the sole pinned core at
  `044a77b3b6af4ce16138d42762f812a20b9f7a89` (`0.21.2`).
- The official Cua Driver v0.28.0 is installed at
  `/Applications/CuaDriver.app`; the CLI is `~/.local/bin/cua-driver`.
- The installed driver identity is `com.trycua.driver`, team `YCK386LBJ7`.
- The user granted Accessibility and Screen Recording only to CuaDriver.
- Authoritative runtime readiness reported consent current,
  `computer_use_health=healthy`, and `execution_ready=true`.
- The native JL app remains an IPC/status/consent client. Python remains the
  policy and execution authority; neither receives computer-use TCC.

No TCC bypass, System Settings automation, unrestricted mode, unsigned-driver
override, LaunchAgent, privileged helper, or alternate computer-use engine was
introduced.

## Live smoke evidence

The accepted live proof was the exact read-only AX capture:

```text
Capability: core.hermes.computer-use
Action: computer_use
Permissions: screen.capture
Target: com.jlagent.control
Foreground: com.jlagent.control
Arguments: {"action":"capture","mode":"ax","app":"com.jlagent.control"}
Target is within workspace: OFF
Reversible: ON
```

Request `5ce6215c-2aab-4d80-9807-0b23a7d370b9` consumed one exact approval and
reached `execution_completed` after 18,619 ms through authenticated IPC, JL
policy/consent, final execution gate, pinned Hermes native tool dispatch, and
CuaDriver. It was not retried.

The native UI initially displayed a timeout because its two-second control
deadline was also used for synchronous tool execution. `b07e3bb` gives only
`execute` a bounded 90-second response deadline; status, activity, prepare, and
consent retain two seconds. The user accepted the already successful backend
proof and explicitly prohibited a post-fix live retry.

## Phase 4C implementation commits

- `a10e7d0` — audit Phase 4C computer-use host.
- `f222698` — add reviewed Cua Driver provisioning.
- `5abb16b` — harden host readiness and lifecycle.
- `37220e7` — checkpoint before authorized host setup.
- `ec7f646` — use the pinned middleware-preserving Hermes tool dispatcher.
- `554d77d` — reject expired native consent safely.
- `b07e3bb` — allow bounded long execution responses.

## Final deterministic evidence

```text
All JL-owned Python tests       106 passed
Ruff / ty / pip check           passed
Secret-pattern / diff checks    passed
Swift format lint               passed
Native deterministic tests      13 passed
Native release build/sign       passed
Hermes full suite               intentionally not run
```

The adapter dispatches an already gate-authorized exact tool call through
`model_tools.handle_function_call`; it does not initialize an LLM `AIAgent` or
provider. JL's authenticated IPC, health, policy, exact consent, caller/session
and target/foreground binding, single-use preparation, final revalidation,
tool/toolset projection, error categorization, and metadata-only audit remain
unchanged.

## Stop boundary

Phase 4C has no remaining work. Verify current Git and host process state before
any future task because recorded PIDs and readiness are time-specific. Do not
start Phase 5, add autonomous workflows, or broaden computer-use permissions
without explicit authorization.
