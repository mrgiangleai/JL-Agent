# JL Agent

JL Agent is a personal macOS AI assistant built around an upstream-first
integration strategy. Phase 1 selects [Hermes Agent](https://github.com/NousResearch/hermes-agent)
as the agent core and pins it as a Git submodule. JL Agent-specific policy,
adapters, routing, health checks, and the future native macOS shell remain
outside the upstream checkout.

## Phase 2 control layer

Phase 2 adds a small Python control package under `src/jl_agent/control/` for
the Capability Registry, read-only Hermes projection, health evaluation,
permission/risk decisions, deterministic model routing, and an inert integrated
invocation plan. Hermes remains the sole runtime core.

Use Python 3.11-3.13:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check src tests
.venv/bin/ty check
```

See [the Phase 2 handoff](docs/PHASE2_HANDOFF.md) for the current source state,
validation commands, safety invariants, and Phase 3 boundary.

## Hermes baseline

Prerequisites: macOS on Apple Silicon, Git, and Python 3.11-3.13. Python 3.13
is currently supported by the pinned Hermes revision. Provider credentials are
not required for installation, import checks, or CLI help.

```bash
git submodule update --init --recursive
./scripts/bootstrap-hermes.sh --dev
./scripts/verify-baseline.sh
```

To inspect the CLI without configuring a provider:

```bash
.venv/bin/hermes --help
```

The bootstrap script keeps generated state in the repository-local `.venv` and
ignored `.jl-agent/` cache. It does not run the interactive setup, write API
keys, or modify the pinned upstream.

## Repository boundaries

- `upstream/hermes-agent/`: immutable, pinned Hermes source of truth.
- `adapters/`: JL Agent-owned compatibility layers around upstream interfaces.
- `mcp/`: declarations and policy for external MCP servers; no vendored servers.
- `skills/`: JL Agent-specific skills only; do not duplicate Hermes skills.
- `config/`: committed examples and schemas; real credentials stay outside Git.
- `docs/`: audit evidence, architecture, routing, registry, and security decisions.
- `macos-app/`: boundary documentation for the future native app; no SwiftUI
  implementation exists yet.

See [the Phase 1 report](docs/PHASE1_REPORT.md) for the audited commits,
validation results, risks, and exact Phase 2 scope.
