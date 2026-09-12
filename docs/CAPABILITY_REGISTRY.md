# Capability Registry design

Status: Phase 1 interface contract. No runtime registry is implemented yet.

## Purpose

The registry is JL Agent's inventory of callable or user-visible capabilities.
It does not replace Hermes' tool/plugin registries. It projects Hermes and
external components into one stable, inspectable contract for the model router,
permission engine, health monitor, and macOS app.

## Descriptor

Every descriptor must contain these fields:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Globally unique, stable identifier such as `core.hermes.agent`. |
| `name` | string | Human-readable name. |
| `version` | string | Component or contract version; never inferred from display text. |
| `source` | object | Kind, canonical repository/package, and immutable revision or version. |
| `enabled` | boolean | Operator intent. This is separate from health. |
| `health` | object | Current state and the check used to derive it. |
| `permissions` | string array | Permission scopes required before invocation. |
| `dependencies` | string array | Runtime, binary, service, or capability dependencies. |
| `entrypoint` | object | Invocation kind and address: plugin, MCP, IPC, executable, or Hermes tool. |
| `capability_type` | enum | One of `agent-core`, `tool`, `skill`, `model-provider`, `memory`, `scheduler`, `ui`, or `service`. |
| `configuration_requirements` | object | Required and optional non-secret configuration keys. |

Recommended optional fields are `description`, `platforms`, `modalities`,
`cost_class`, `data_boundary`, `risk_class`, `fallbacks`, and `maintainer`.

## Health state

`health.state` is one of `unknown`, `starting`, `healthy`, `degraded`,
`unavailable`, or `blocked`. A health check must be bounded by a timeout and
must not trigger billable model calls, permission prompts, package installs, or
destructive recovery. Health is observed state; `enabled` remains user policy.

## Permission scopes

Scopes use dotted names and least privilege, for example `local.read`,
`local.write.reversible`, `local.delete`, `screen.capture`, `input.control`,
`network.read`, `external.send`, `credential.use`, and `finance.transact`.
The permission engine, not the capability, makes the final confirmation
decision.

## Registration and resolution

1. Built-in JL descriptors load from signed/committed configuration.
2. The Hermes adapter projects selected Hermes toolsets without copying their
   implementation.
3. External MCP/plugin descriptors are accepted only after schema, source,
   license, and permission validation.
4. Duplicate IDs fail closed. A component cannot silently replace another.
5. Dependencies resolve by capability ID or an explicit runtime requirement.
6. The router sees only enabled capabilities whose dependencies are satisfied
   and whose health is `healthy` or explicitly tolerated as `degraded`.
7. Every invocation records descriptor ID/version, decision, permissions, and
   result status in the audit stream; secrets and raw sensitive payloads are
   excluded.

## Ownership boundaries

- Hermes remains authoritative for its native tool schema and execution.
- JL Agent owns normalization, policy metadata, health projection, and UI
  presentation.
- MCP servers remain separate processes and never mutate the registry directly.
- The macOS app is a read/approve/disable client; it cannot forge health or
  bypass the permission engine.

A non-normative example is committed at `config/capabilities.example.yaml`.
