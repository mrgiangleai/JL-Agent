# Phase 2 handoff

## Read this first

Phase 2 is complete. Hermes Agent remains JL Agent's single runtime core. The
new code is a thin control layer that prepares policy-approved references for
Hermes; it does not execute a second agent loop.

Before Phase 3, read in this order:

1. `docs/PHASE2_REPORT.md`
2. `docs/ARCHITECTURE.md`
3. `docs/CAPABILITY_REGISTRY.md`
4. `docs/SECURITY_MODEL.md`
5. `docs/MODEL_ROUTER.md`
6. `docs/COMPONENT_MATRIX.md`
7. `docs/PROGRESS.md`

## Source state

- Branch: `main`, tracking `origin/main`.
- Hermes path: `upstream/hermes-agent`.
- Hermes revision: `044a77b3b6af4ce16138d42762f812a20b9f7a89`.
- Hermes package version: `0.21.2`.
- Expected state after the final documentation commit is pushed: clean local
  tree, `main` synchronized with `origin/main`, and clean Hermes submodule.

Phase 2 logical commits:

| Commit | Scope |
|---|---|
| `4f00221` | Capability Registry parser and validator |
| `cb80f1c` | Read-only Hermes capability projection |
| `bf5d03c` | Lightweight Health Monitor |
| `2f0cc11` | Permission / Risk Engine |
| `721da01` | Deterministic Model Router |
| `0ca5bbe` | Integrated JL control path |
| `1064dd6` | Lint/type/dependency validation fixes |

## Important paths

- `src/jl_agent/control/registry.py`: descriptor parser/validator.
- `src/jl_agent/control/hermes_projection.py`: pinned Hermes identity and the
  representative files/terminal/browser/memory/MCP projection.
- `src/jl_agent/control/health.py`: side-effect-free health derivation.
- `src/jl_agent/control/permissions.py`: action classification, decision, and
  exact-action fingerprint.
- `src/jl_agent/control/router.py`: deterministic provider-neutral routing.
- `src/jl_agent/control/control_plane.py`: integrated preparation facade.
- `tests/`: 30 JL-owned focused and end-to-end tests.
- `config/capabilities.example.yaml`: registry contract fixture.
- `config/model-router.example.yaml`: router policy fixture.
- `pyproject.toml`: Python 3.11-3.13 package and pinned development tools.

## Control path contract

`JLControlPlane.prepare()` performs these bounded operations:

1. Validate the pinned Hermes checkout and produce read-only descriptors.
2. Derive capability health from cheap local facts.
3. Reject non-healthy capabilities unless degraded health is explicitly
   tolerated.
4. Evaluate the exact proposed action against descriptor permissions.
5. Stop if the action is denied or needs confirmation.
6. Filter and order provider-neutral model candidates.
7. Return an inert Hermes capability/provider/model reference.

The result does not call Hermes. A future adapter must pass the selected
provider/model and toolset through supported Hermes configuration or constructor
surfaces. Do not patch `run_agent.py` or duplicate provider fallback.

## Safety invariants to preserve

- JL policy may make Hermes stricter but may never weaken an upstream denial.
- Unknown permission scopes and undeclared capability permissions fail closed.
- Unattended destructive, external communication, credential-sensitive, and
  financial/high-risk actions remain denied.
- Approval must bind to the emitted fingerprint and exact normalized tuple;
  changed arguments, target, capability version, caller, session, or foreground
  app require a new approval.
- Local-only, off-device, residency, ability, and cost constraints survive the
  full fallback chain.
- Health checks must remain cheap, bounded, non-billable, non-destructive, and
  unable to start expensive services.
- Never place JL code inside `upstream/hermes-agent` without a documented,
  source-audited concrete gap.

## Setup and checks

Use any Python 3.11-3.13 environment. A small JL-only setup is:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Run checks sequentially:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check src tests
.venv/bin/ty check
.venv/bin/python -m pip check
git submodule status
git -C upstream/hermes-agent status --short
git diff --check
git status --short --branch
```

Do not bootstrap Hermes or run its approximately 41,000 tests unless a Phase 3
change actually touches the upstream boundary. Current Phase 2 projection tests
validate the relevant pinned source surface without importing Hermes.

## Recommended Phase 3 starting point

Wait for and follow the explicit Phase 3 scope. If it continues the current
architecture, the smallest security-critical next slice is an authenticated,
versioned, user-owned local IPC boundary plus one-time approval consumption for
the exact fingerprint already emitted by the policy engine. Keep the client a
status/consent/approval surface; it must not execute tools directly.

Do not begin voice, computer control, local LLM startup, external frameworks, or
unattended side effects unless Phase 3 explicitly requires them. macOS TCC and
native UI remain unimplemented and unvalidated.
