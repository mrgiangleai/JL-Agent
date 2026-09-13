# Phase 2 report

## Outcome

Phase 2 implements a thin, tested JL-owned control layer above the pinned
Hermes core. It inventories selected Hermes capabilities, derives cheap health,
classifies permission risk, chooses a deterministic model route, and prepares a
Hermes invocation reference. It does not run a second agent/tool loop.

## Implementation

### Capability Registry

`src/jl_agent/control/registry.py` implements the schema-v1 contract represented
by `config/capabilities.example.yaml`.

- Required descriptor fields are typed as immutable dataclasses.
- Capability type, health state, and entrypoint kind are constrained enums.
- Source identity requires a canonical repository/package and immutable
  revision/version.
- Unknown fields, duplicate YAML keys, duplicate capability IDs, invalid types,
  duplicate list values, and invalid configuration declarations fail closed.
- Recommended optional metadata remains available without becoming execution
  behavior.

### Hermes projection

`src/jl_agent/control/hermes_projection.py` projects five representative Hermes
capabilities: files, terminal, browser, memory, and MCP.

- Every descriptor references Hermes version `0.21.2`, repository URL, and pin
  `044a77b3b6af4ce16138d42762f812a20b9f7a89`.
- Identity is validated from `pyproject.toml` and Git HEAD.
- Availability reads bounded source markers only.
- The adapter never imports Hermes modules, triggers plugin discovery, starts
  MCP/browser services, or copies an upstream implementation.

### Health Monitor

`src/jl_agent/control/health.py` derives health from operator enablement,
required configuration, implementation availability, dependency states, and an
already-computed adapter probe outcome.

The output states used in Phase 2 are `healthy`, `degraded`, `unavailable`,
`misconfigured`, and `disabled`. Missing implementation or dependency state
fails closed. The monitor does not execute arbitrary probes or recovery.

### Permission / Risk Engine

`src/jl_agent/control/permissions.py` implements the executable portion of
`docs/SECURITY_MODEL.md` for:

- read-only;
- reversible local;
- destructive;
- external communication;
- credential-sensitive; and
- financial/high-risk actions.

It emits `may-proceed`, `requires-confirmation`, or `must-be-denied`. Permission
scopes must be known and be a subset of the capability descriptor. Hermes or
another upstream denial is never weakened. Destructive, external, credential,
and financial actions are denied when unattended. Confirmation requirements
fail closed when the approval surface is unavailable.

Each decision includes a SHA-256 binding over normalized arguments, resolved
target, capability ID/version, caller, session, foreground app, and requested
permissions. Phase 2 does not yet issue or consume approval tokens.

### Deterministic Model Router

`src/jl_agent/control/router.py` loads the policy in
`config/model-router.example.yaml` and routes over provider-neutral candidates.
It does not require credentials or a named provider.

Hard filters cover enabled/health state, abilities, local-only and off-device
policy, provider allowlist, data residency, cost, budget confirmation, and
context size. Remaining candidates are ordered by observable rules for simple,
coding, vision, web/research, and high-reasoning tasks. Fallbacks stay inside
the same hard constraints, are attempt-bounded, and cannot silently escalate a
selected local route to cloud under the default policy. Hermes remains
responsible for actual provider calls and execution-level retry.

### Integrated control path

`src/jl_agent/control/control_plane.py` composes the contracts. It validates the
Hermes projection, updates registry health, evaluates permission policy, routes
a fake/provider-neutral model candidate, then returns an inert
`HermesInvocationProjection`. A non-healthy capability stops before policy and
routing; confirmation or denial stops before route/invocation construction.

The integration test proves a local read-only file request reaches the pinned
Hermes `file` toolset reference. It also proves an out-of-workspace write stops
at confirmation without calling the router. No Hermes agent loop is invoked.

## Validation evidence

Validation was intentionally lightweight and sequential.

| Check | Result |
|---|---|
| JL-owned unit/integration tests | 30 passed |
| Ruff `0.15.10` | Passed |
| `ty` `0.0.21` | Passed |
| Installed dependency validation | `pip check` passed |
| Hermes submodule identity | Clean at expected pin |
| Secret-pattern scan | No matches |
| Whitespace/diff validation | Passed |
| Full Hermes suite | Not run by design |

The tests cover the committed capability fixture and router fixture, negative
schema paths, pin mismatch, missing Hermes modules, all required health states,
all six risk classes, exact-action fingerprinting, provider hard filters,
fallback limits, and the integrated control path.

## Review findings

- Duplicated functionality: none found. JL stores descriptors and decisions;
  Hermes keeps tool schemas, tool execution, providers, fallback execution,
  memory, browser, terminal, and MCP implementations.
- Dependencies: PyYAML is the only runtime dependency and is required by the
  existing YAML contracts. Test discovery uses the standard library.
- Hermes modifications: none; the submodule worktree and pin are unchanged.
- Secrets: none found or added. Tests use fake provider names and no network.
- Generated artifacts: project bytecode, Ruff cache, and editable-install
  metadata were removed/ignored; no build output is committed.
- Heavy resources: no Hermes environment, local LLM, browser, MCP server, or
  long-running process was started.

## Remaining gaps

- Approval fingerprints are produced, but secure short-lived approval issuance,
  storage, expiry, and one-time consumption do not exist yet.
- No authenticated/versioned local IPC surface exists.
- Provider availability must later be supplied by reviewed Hermes/provider
  adapters; Phase 2 tests use fake candidates.
- Projection availability is deliberately static and representative, not a
  replacement for Hermes' dynamic registry.
- Health has no active recovery and accepts only cheap precomputed probe
  outcomes.
- macOS TCC status, native UI, voice, and computer control remain untouched.
