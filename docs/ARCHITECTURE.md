# JL Agent target architecture

## Decision

Hermes Agent is the single main core. JL Agent composes around its supported
interfaces and keeps the pinned upstream checkout unmodified. OpenJarvis and
PersonalJarvis are design/component references, not secondary agent runtimes.

```text
macOS Native App
       |
       | versioned local IPC; status, consent, approvals
       v
JL Runtime Boundary
  +-- Capability Registry <---- Health Monitor
  +-- Permission / Risk Engine
  +-- Model Router
  +-- Adapters
  +-- MCP process boundary
  +-- JL-specific Skills
       |
       | supported config, plugin hooks, MCP, subprocess/API
       v
Hermes Core (pinned Git submodule)
  +-- agent loop / providers / fallback
  +-- tools / skills / memory / sessions
  +-- browser / vision / terminal / files
  +-- voice / wake / cron / delegation
  +-- logging / dashboard / gateways
```

## Module boundaries

### Hermes Core

Owns conversation and tool loops, provider protocol clients, session state,
memory, native tools, skills, MCP client behavior, scheduling, delegation, and
upstream logging. It lives at `upstream/hermes-agent` as a pinned submodule.
JL code must not be placed inside this directory. Upgrades are deliberate
revision changes followed by the verification and compatibility suites.

### Adapters

Translate versioned JL contracts to supported Hermes configuration, plugins,
MCP, local IPC, APIs, or subprocesses. Each adapter owns only its translation
and lifecycle. It cannot import arbitrary Hermes internals, edit upstream
files, own policy decisions, or bypass the capability registry.

Phase 3B's execution adapter is deliberately narrower. It constructs pinned
Hermes `run_agent.AIAgent` configuration from the selected provider, model,
fallback chain, toolset, and session, then calls Hermes'
`agent.agent_runtime_helpers.invoke_tool()` with the one exact tool and
canonical arguments. That helper retains Hermes request/execution middleware,
plugin pre-tool hooks, guardrails, enabled-tool validation, registry dispatch,
and native tool implementation. JL does not run a second agent loop or copy
fallback, registry, session, memory, approval, or tool-execution logic.

### MCP

The preferred boundary for independently deployable tools. Each server is a
separate process/service with a pinned source/version, least-privilege
environment, bounded startup and call timeouts, health probe, and declared
data boundary. MCP output is untrusted input. Server credentials are injected
at runtime and never stored in committed manifests.

### Skills

Hermes' skills system remains authoritative. `skills/` contains only JL-specific
procedural knowledge. Skills cannot become a security boundary: installation
requires source/license review because skill code may run with agent-process
privileges.

### Capability Registry

Provides the stable inventory consumed by UI, router, permission engine, and
health monitor. It projects existing implementations rather than becoming a
second tool registry. The descriptor contract is defined in
`CAPABILITY_REGISTRY.md`.

### Permission / Risk Engine

Makes the final allow, deny, or confirm decision immediately before a side
effect. Hermes approval remains defense in depth. JL policy cannot weaken an
upstream denial. The engine binds approval to exact normalized arguments,
target, descriptor version, and expiry; changes invalidate approval.

### Health Monitor

Runs non-billable, non-destructive, time-bounded probes and updates registry
health. It never auto-installs packages, asks for macOS permissions, restarts a
crashing component indefinitely, or marks a capability enabled. Recovery is
rate-limited and observable.

### Model Router

Classifies the turn, filters models by hard constraints, and emits an ordered,
policy-compliant provider/model chain. Hermes performs the actual calls and
fallback. See `MODEL_ROUTER.md`.

### macOS Native App

The Phase 4A SwiftUI application is a native control/consent client. It owns a
compact status, request/result, consent, and safe-activity surface. It speaks
protocol v1 over bounded user-local Unix sockets, stores its bearer credential
and consent private key in Keychain, and contains no Hermes imports, tool
implementations, provider logic, or execution authority.

Normal authenticated IPC exposes status, safe activity, prepare, and execute.
A separate private consent socket accepts only an opaque pending-consent ID,
an approve/reject decision, and a Keychain-key signature. The runtime owns the
pending exact request and authoritative fingerprint, issues and consumes the
one-time approval internally, and registers the prepared request with the
existing gate. Approval tokens and client-computed fingerprints never cross a
socket. See `PHASE4A_TRUST_BOUNDARY.md`.

The foreground runtime remains operator-started. Phase 4A does not install a
LaunchAgent, manage final distribution signing, or add voice/computer control.

### Execution Gate and Audit Ledger

The execution gate owns an in-memory, single-use prepared record. Immediately
before dispatch it rechecks authenticated caller/session identity, preparation
TTL, exact Phase 2 fingerprint and consumed approval, current capability
identity/version/health, upstream denial, and deterministic route. Any drift
denies execution. The adapter accepts only an internal gate-issued command.

Security events are appended to a private bounded JSONL ledger. The ledger
stores allowlisted metadata and hashed references, never credentials, tokens,
full action arguments, user content, or model reasoning.

## Data and control flow

1. A typed exact action enters through the native app or a reviewed Hermes
   gateway surface.
2. The runtime attaches caller/session identity and queries enabled healthy
   capabilities.
3. The Model Router selects a policy-compliant provider/model chain.
4. Hermes runs the turn and proposes tool invocations.
5. The Permission / Risk Engine evaluates the exact action. Confirmation, when
   required, is shown by the native app and is fail-closed on timeout.
6. Hermes or the selected adapter executes the action.
7. Health and audit events record metadata and outcomes without secrets.

For the Phase 3B direct execution boundary, steps 4-6 are constrained to one
already prepared tool projection: JL performs final revalidation and Hermes'
native invocation helper performs the exact tool dispatch. This is not a second
conversation/agent loop.

In Phase 4A, a confirmation path pauses after step 5. The runtime returns only
safe consent presentation data and an opaque signed challenge. A trusted native
decision resumes the stored exact request through the same preparation and
execution gate; the app cannot replace that request while approving it.

## External component rule

An external component may integrate only through an adapter, MCP, API,
subprocess, or a reviewed Hermes plugin. It cannot directly modify unrelated
Hermes internals. Copying code requires a documented gap, license/NOTICE
compliance, provenance at file level, and a maintenance owner. Architectural
inspiration alone is preferred when a small JL contract avoids importing a
second framework.

## Baseline layout

```text
adapters/                 JL translation boundaries
config/                   non-secret examples
docs/                     decisions and contracts
macos-app/                native SwiftUI control/consent client
mcp/                      reviewed MCP manifests/policy
scripts/                  bootstrap and verification
skills/                   JL-only skills
upstream/hermes-agent/    pinned, unmodified Git submodule
```
