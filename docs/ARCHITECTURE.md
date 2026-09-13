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

The future SwiftUI application owns native lifecycle, onboarding, microphone/
screen/accessibility permission education, approval sheets, status, and local
notifications. It is not allowed to execute tool calls directly. All actions
flow through the runtime permission engine. Phase 3A implements only the
authenticated local transport and consent-consumption contracts; it contains no
SwiftUI implementation.

## Data and control flow

1. A typed or spoken request enters through the native app or a reviewed Hermes
   gateway surface.
2. The runtime attaches caller/session identity and queries enabled healthy
   capabilities.
3. The Model Router selects a policy-compliant provider/model chain.
4. Hermes runs the turn and proposes tool invocations.
5. The Permission / Risk Engine evaluates the exact action. Confirmation, when
   required, is shown by the native app and is fail-closed on timeout.
6. Hermes or the selected adapter executes the action.
7. Health and audit events record metadata and outcomes without secrets.

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
macos-app/                future native-client boundary only
mcp/                      reviewed MCP manifests/policy
scripts/                  bootstrap and verification
skills/                   JL-only skills
upstream/hermes-agent/    pinned, unmodified Git submodule
```
