# Phase 1 handoff

## Current architecture

Hermes Agent is JL Agent's single runtime core. JL-owned functionality composes
around the pinned upstream through stable adapters, MCP, plugins, subprocesses,
APIs, or versioned local IPC. The target layers are:

1. Hermes Core
2. JL Adapters and MCP integrations
3. Capability Registry and Health Monitor
4. Model Router
5. Permission / Risk Engine
6. JL-specific Skills
7. Future macOS native app as a status/consent/approval client

The macOS app must never execute tools directly or become a second agent core.
External components must not modify unrelated Hermes internals.

## Pinned upstream

- Repository: `https://github.com/NousResearch/hermes-agent.git`
- Path: `upstream/hermes-agent`
- Commit: `044a77b3b6af4ce16138d42762f812a20b9f7a89`
- Package version: `0.21.2`
- License: MIT
- Policy: keep the submodule unmodified; upgrades require an explicit revision
  change plus compatibility/security/test review.

## Important paths

- `docs/COMPONENT_MATRIX.md`: audited per-capability decisions and provenance.
- `docs/ARCHITECTURE.md`: module boundaries and data/control flow.
- `docs/SECURITY_MODEL.md`: risk classes, confirmation rules, macOS permissions.
- `docs/CAPABILITY_REGISTRY.md`: Phase 2 descriptor contract.
- `docs/MODEL_ROUTER.md`: routing and fallback contract.
- `docs/PHASE1_REPORT.md`: audit evidence, validation results, risks, Phase 2 order.
- `docs/PROGRESS.md`: final Phase 1 execution/resource state.
- `config/capabilities.example.yaml`: registry descriptor example.
- `config/model-router.example.yaml`: router policy example.
- `scripts/bootstrap-hermes.sh`: repository-local base or CI-extras installation.
- `scripts/verify-baseline.sh`: pin/import/CLI verification with project-local state.
- `adapters/`, `mcp/`, `skills/`: JL-owned integration boundaries.
- `macos-app/`: native-client boundary only; no SwiftUI code exists yet.

## Decisions already made

- Keep Hermes as the only core.
- OpenJarvis is not embedded. Its routing/optimization work is inspiration only;
  Apple Foundation Models may later be an isolated optional provider adapter.
- PersonalJarvis is not embedded. Only narrowly adapt its macOS permission,
  foreground-target, stale-frame, action-ledger, verification, and visible
  approval/control patterns when a concrete gap is proven.
- JL owns the thin Capability Registry projection, deterministic Model Router,
  cross-capability Health Monitor, Permission/Risk Engine, local IPC, and native
  consent/status UI.
- Independently maintained capabilities prefer reviewed, pinned MCP or process/
  API isolation. Copying Apache-2.0 code requires file-level provenance and
  LICENSE/NOTICE handling.
- No provider/model is mandatory. Routing must preserve user locality, privacy,
  capability, cost, and data-boundary constraints across fallback.

## Components that must not be rebuilt

Do not duplicate Hermes' agent/tool loop, provider clients and execution
fallback, session/memory stores, tool registry, skills runtime, MCP client,
browser automation, terminal/files, vision normalization, computer-use backend,
STT/TTS/wake engine, scheduler/cron, proactive/background substrate,
subagent/delegation lifecycle, command-danger detection, configuration/profiles,
logging/observability, or operational dashboard. Extend them only through the
boundaries above unless a documented source audit proves a concrete gap.

## Known macOS issues

- Host verified in Phase 1: Apple Silicon `arm64`, macOS 26.5.1, Python 3.13.1.
  Hermes declares Python `>=3.11,<3.14` and installed/imported successfully.
- Only Xcode Command Line Tools are installed. The optional OpenJarvis Apple
  Foundation Models route requires full Xcode, Apple Intelligence availability,
  macOS 26+, and `apple-fm-sdk`.
- `CuaDriver.app` is not installed. Hermes' Darwin private computer-use path
  deliberately requires a signed app to preserve TCC identity. One generic
  full-suite test failed because it mocked a bare driver executable while the
  Darwin branch required the app.
- Microphone, audio devices, wake word, Screen Recording, Accessibility, and
  Input Monitoring were not validated interactively. Hermes has an explicit
  macOS ARM64 TFLite bridge for wake word, but hardware/TCC testing remains.
- No provider credential was supplied; no paid/live model request was made.
- The upstream full suite was stopped under the resource-safe policy after more
  than 13,000 passes and two recorded failures. The second failure output was
  truncated. This is a documented coverage gap, not a pass.

## Bootstrap and setup

Generated environments/state are intentionally absent from Git and were removed
after Phase 1 validation to recover disk space.

```bash
git submodule update --init --recursive
./scripts/bootstrap-hermes.sh
./scripts/verify-baseline.sh
```

Use the larger test environment only when its cost is justified:

```bash
./scripts/bootstrap-hermes.sh --dev
```

The bootstrap stores the venv and pip cache under ignored project-local paths.
It does not run interactive setup or store credentials.

## Validation commands

Run lightweight checks first and keep them sequential:

```bash
git submodule status
git -C upstream/hermes-agent status --short
bash -n scripts/bootstrap-hermes.sh scripts/verify-baseline.sh
./scripts/verify-baseline.sh
.venv/bin/python -m pip check
git diff --check
git status --short --branch
```

After `--dev`, upstream lint is:

```bash
cd upstream/hermes-agent
../../.venv/bin/ruff check .
```

Do not start the ~41,000-test suite by default. Before changing the Hermes pin,
run the upstream Linux full lane and the official `macos_only` lane in suitable
CI environments. Keep `HOME`, `HERMES_HOME`, and test caches inside the project
for any local targeted test.

## Git state at handoff

- Branch: `main`
- Remote: `origin` -> `https://github.com/mrgiangleai/JL-Agent.git`
- Phase 1 baseline commit: `a303939`
- Phase 1 audit/report commit: `37061b5`
- Expected final state after this handoff commit is pushed: clean working tree,
  local `main` synchronized with `origin/main`, clean Hermes submodule at the
  pinned commit.

## Recommended Phase 2 starting point

Start only with a small, tested Capability Registry parser/validator for the
contract in `docs/CAPABILITY_REGISTRY.md`, then project a minimal read-only set
of Hermes capabilities. Do not begin SwiftUI, voice, computer use, learned model
routing, or unattended actions until registry identity/version/health and the
Permission/Risk Engine boundary are executable and tested.
