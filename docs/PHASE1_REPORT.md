# Phase 1 report

Date: 2026-09-12

Host: Apple Silicon (`arm64`), macOS 26.5.1

Scope: audit, architecture, security design, and a maintainable Hermes baseline;
no native macOS UI implementation.

## Executive result

Phase 1 selects Hermes Agent as JL Agent's only agent core. Hermes is included
without local source changes as a pinned Git submodule at revision
`044a77b3b6af4ce16138d42762f812a20b9f7a89` (`0.21.2`, MIT). OpenJarvis and
PersonalJarvis are not embedded as secondary runtimes: their useful ideas are
limited to narrow, isolated adapters or design references recorded in
`COMPONENT_MATRIX.md`.

The repository now has explicit upstream, adapter, MCP, skill, configuration,
documentation, and future native-app boundaries. Capability Registry, Model
Router, and permission/health contracts are designed but intentionally not
overimplemented in Phase 1.

## Repository and audit evidence

The original repository contained only `README.md` and the MIT `LICENSE`, with a
clean `main` branch tracking `origin/main`. Existing history was preserved.

The audit inspected actual source, packaging metadata, tests, CI workflows,
security documentation, and licenses at these immutable revisions:

| Project | Revision | Version/status | License | Audit focus |
|---|---|---|---|---|
| Hermes Agent | `044a77b3b6af4ce16138d42762f812a20b9f7a89` | `0.21.2` | MIT | Agent/runtime, providers, memory, tools/skills, MCP, browser/vision/computer use, voice/wake, scheduler, delegation, safety, config, observability, UI, macOS, tests/dependencies |
| OpenJarvis | `b1055c983b25b298c7e97723847d215df18de4a8` | Alpha; dynamic package version | Apache-2.0 | Registry/types, routing/learning, Apple Foundation Models, overlapping runtime surface |
| PersonalJarvis | `e56c43b0289643f738199abc02ce30d7bdbc8c0f` | `2.1.0`, Beta | Apache-2.0 | Voice loop/wake resilience, macOS permissions, target guards, computer-use ledger/verification, approval UX |

Representative inspected paths include:

- Hermes: `run_agent.py`, `agent/agent_init.py`, `agent/turn_facade.py`,
  `providers/`, `hermes_state*.py`, `model_tools.py`, `toolsets.py`, `tools/`,
  `cron/`, `plugins/`, `hermes_cli/`, `apps/desktop/`, `SECURITY.md`,
  `pyproject.toml`, and `.github/workflows/`.
- OpenJarvis: `src/openjarvis/core/registry.py`, `core/types.py`,
  `learning/routing/`, `learning/agents/`, `engine/apple_fm.py`, memory/tool/MCP/
  scheduler/security/speech modules, packaging metadata, and license.
- PersonalJarvis: `jarvis/speech/pipeline.py`, `rolling_whisper_wake.py`,
  `wake_verifier.py`, `echo_guard.py`, `watchdog.py`, `jarvis/cu/target_guard.py`,
  `ledger.py`, `verify.py`, `jarvis/platform/permissions.py`, `macos_ax.py`,
  `window_capture.py`, `jarvis/safety/risk_tier.py`, `approval.py`,
  `tool_executor.py`, packaging metadata, and license.

Dependency inspection found 34 direct core dependencies and 44 optional-extra
entries in Hermes, 13 core and 56 optional-extra entries in OpenJarvis, and 57
core and 11 optional-extra entries in PersonalJarvis at the audited revisions.
This reinforced the decision not to combine overlapping runtimes.

## Decisions

- Keep Hermes for the agent loop, provider protocols/fallback, session memory,
  tools, skills, MCP, browser, terminal/files, vision, computer-use integration,
  STT/TTS/wake, scheduling, background behavior, delegation, configuration,
  logging, and operational web/desktop surfaces.
- Write small JL-owned Capability Registry projection, Model Router policy,
  Permission/Risk Engine, and cross-capability Health Monitor.
- Evaluate OpenJarvis Apple Foundation Models only as an isolated future
  provider adapter. Reuse routing and optimization ideas, not its runtime.
- Adapt PersonalJarvis foreground-target validation, stale-frame/postcondition
  checks, permission diagnostics, and visible approval/control UX only where
  they close a concrete gap. Retain its voice-resilience work as test/UX ideas.
- Integrate independently maintained tools through reviewed, pinned MCP servers
  or subprocess/API boundaries rather than vendoring them into Hermes.

The evidence and per-capability rationale are in `COMPONENT_MATRIX.md`.

## Working baseline

`upstream/hermes-agent` is a Git submodule whose canonical remote is the
official Hermes repository. The top-level bootstrap creates `.venv` inside JL
Agent and performs an editable install. Base mode installs Hermes' core
dependencies; `--dev` installs the same optional groups exercised by upstream
CI so the pinned suite can be validated. No setup wizard is run and no
credential is requested or stored.

```bash
git submodule update --init --recursive
./scripts/bootstrap-hermes.sh --dev
./scripts/verify-baseline.sh
```

Runtime state used during verification is redirected into ignored `.jl-agent/`
paths. Committed examples contain identifiers only; provider credentials remain
runtime configuration.

After validation, the reproducible `.venv`, `.jl-agent` state, and Python test/
lint caches were removed because the external volume's allocation behavior made
the full-extras environment and many small cache files consume tens of GiB.
This increased free space from about 4.6 GiB to about 90 GiB. Re-run the
bootstrap command above whenever a local runtime environment is needed.

## Validation

| Check | Result |
|---|---|
| Hermes pinned revision/license and clean submodule | PASS |
| Python 3.13.1 editable installation with CI-equivalent extras | PASS |
| Package dependency consistency (`pip check`) | PASS, no broken requirements |
| Core imports (`run_agent`, `model_tools`, `tools.mcp_tool`) | PASS |
| CLI startup surface (`hermes --help`) | PASS |
| Bootstrap/verification shell syntax (`bash -n`) | PASS |
| Example configuration YAML parsing/schema version | PASS |
| Upstream blocking lint (`ruff check .`) | PASS; three existing malformed-noqa warnings |
| Upstream typecheck (`ty check`) | INCONCLUSIVE: completed with no diagnostics, but the final process exit code was lost during the session handoff |
| Canonical isolated upstream suite (`scripts/run_tests.sh`, 8 workers) | PARTIAL: more than 13,000 tests passed before interruption; two failures had been recorded on the macOS generic full-suite path |
| Secret-pattern scan outside ignored/upstream trees | PASS, no candidate file |

The first broad pytest attempt used one shared process inside the workspace
sandbox. That is not Hermes' supported test mode: socket/process isolation was
blocked and shared global state produced misleading failures. The later run
used the upstream `scripts/run_tests.sh` per-file subprocess runner outside that
sandbox, while redirecting user state into the project. Initial collection also
identified optional provider dependencies expected by upstream CI; the
bootstrap was corrected to install the exact CI extra groups before that run.

The canonical runner was interrupted when execution switched to the requested
resource-safe sequential model. At that point more than 13,000 tests had passed
and two failures were recorded. One is fully identified:
`tests/computer_use/test_cua_no_overlay.py` mocks a bare
`/usr/bin/cua-driver`, while the real Darwin branch requires a signed
`CuaDriver.app` so macOS TCC identity is preserved. Installing that external
application outside the project was intentionally not attempted. The second
failure's inline output was truncated before the handoff and cannot be
recovered from the runner, which deletes its per-file temporary output. The
~41,000-test suite was not restarted: the prior full-extras environment and
test caches had consumed tens of GiB on this volume, and the user explicitly
requested a lighter sequential validation strategy. This is an honestly
documented coverage gap, not a claimed pass.

## macOS and Apple Silicon findings

- The selected Hermes Python range is `>=3.11,<3.14`; the repository's Python
  3.13.1 installation works on this Apple Silicon host.
- Hermes has explicit macOS paths for voice playback and computer use, and its
  wake-word code documents an ARM64 TensorFlow Lite workaround. Actual
  microphone, audio device, wake word, Screen Recording, and Accessibility
  behavior still require interactive GUI/hardware validation.
- Hermes computer use depends on a separately reviewed `cua-driver` MCP setup;
  it is not enabled by this baseline.
- Only Xcode Command Line Tools are installed. OpenJarvis' Apple Foundation
  Models path requires full Xcode, macOS 26+, Apple Intelligence availability,
  and `apple-fm-sdk`; it remains an optional Phase 2 experiment.
- No provider credential was supplied, so no paid/live model request or
  end-to-end conversational turn was executed. Installation, import, CLI, and
  offline tests do not require credentials.

## Risks and genuine blockers

There is no blocker to the Phase 1 architecture/baseline decision. The
remaining limitations and validation gaps are intentionally deferred or
environment-bound:

- JL's policy engine, approval IPC, capability health service, and native app do
  not exist yet; Hermes' own approval logic is defense in depth, not OS
  containment.
- The fallback installer uses `pip` because `uv` is not installed on this host;
  upstream top-level requirements are pinned, but the `uv.lock` graph is not
  enforced by that path. Phase 2 should standardize a pinned `uv` bootstrap.
- Voice and computer-use code needs real macOS TCC permissions and hardware
  validation. Those prompts cannot be meaningfully verified headlessly.
- The interrupted full suite has one known driver-prerequisite failure and one
  unrecovered failure. Before upgrading the Hermes pin, CI should rerun the
  Linux full lane and official small `macos_only` lane in their supported
  environments.
- Live-provider startup needs a user-selected provider and credential. Nothing
  in Phase 1 assumes or commits one.
- Future Apache-2.0 adaptations require file-level provenance plus LICENSE and
  NOTICE handling; Phase 1 copies no source from either reference repository.

## Exact Phase 2 scope

1. Implement the versioned Capability Registry parser/validator and project a
   minimal Hermes capability set into it.
2. Implement a deterministic Model Router with explicit constraints, decision
   records, and fixtures for simple, coding, vision, research, and high-reasoning
   routes; let Hermes own execution fallback.
3. Implement the Permission/Risk Engine before exposing unattended actions,
   including exact-argument approval binding, deny-on-timeout, audit redaction,
   and tests for all six action classes.
4. Define authenticated, user-local, versioned IPC and build the smallest
   SwiftUI status/approval shell; the app must not execute tools directly.
5. Add Health Monitor probes for Hermes, configured providers, MCP processes,
   and macOS permissions. Probes must be bounded and non-billable.
6. Validate one user-selected text provider end to end, then add voice/wake and
   computer use separately behind disabled-by-default capabilities and real TCC
   permission tests.
7. Prototype Apple Foundation Models only after full Xcode/Apple Intelligence
   prerequisites are confirmed; keep it optional and removable.
8. Adopt pinned `uv` installation/sync, add top-level CI for baseline scripts,
   registry/router/policy tests, license provenance, secret scanning, and
   submodule upgrade verification.
