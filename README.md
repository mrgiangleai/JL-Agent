# JL Agent

JL Agent is a personal macOS AI assistant built around an upstream-first
integration strategy. Phase 1 selects [Hermes Agent](https://github.com/NousResearch/hermes-agent)
as the agent core and pins it as a Git submodule. JL Agent-specific policy,
adapters, routing, health checks, and the future native macOS shell remain
outside the upstream checkout.

## Phase 4A control and consent client

The Python runtime under `src/jl_agent/` owns authenticated local IPC, policy,
health, exact approval, preparation, execution gating, and the thin Hermes
adapter. The native SwiftUI client under `macos-app/` owns local status,
Keychain-backed client credentials, exact consent UI, request/result display,
and safe activity. Hermes remains the sole agent runtime core.

Use Python 3.11-3.13:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check src tests
.venv/bin/ty check
```

See [the Phase 4A handoff](docs/PHASE4A_HANDOFF.md) for the current source state,
validation commands, safety invariants, and deferred Phase 4B boundary.

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
- `macos-app/`: native SwiftUI protocol-v1 control/consent client; never an
  agent runtime or Hermes execution surface.

See [the Phase 1 report](docs/PHASE1_REPORT.md) for the audited commits,
validation results, risks, and exact Phase 2 scope.
