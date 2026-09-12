# Model Router design

Status: Phase 1 policy contract. It deliberately names no mandatory model.

## Boundary

Hermes continues to own provider clients, model invocation, streaming, tool
calls, and configured provider fallback. The JL Model Router sits before a
Hermes turn and selects a route profile. It may choose a model/provider already
available through Hermes or a future isolated provider adapter; it must not
patch the Hermes agent loop.

## Inputs

- Task class and modality: simple, coding, vision, web/research, or
  high-reasoning.
- Required abilities: tool calling, vision, structured output, context size,
  citations, and language.
- User constraints: local-only, allowed providers, maximum cost, latency goal,
  data residency, and explicit model choice.
- Runtime facts from the capability registry: enabled, health, version, and
  dependency state.
- Session facts: estimated tokens, attachments, toolset, retry history, and
  whether sending data off-device is allowed.

## Route profiles

| Profile | Minimum requirements | Default optimization order |
|---|---|---|
| Simple | text and required tool-call protocol | locality, latency, cost |
| Coding | reliable tool calling, code quality, adequate context | correctness, context, latency |
| Vision | image input and requested output mode | correctness, privacy, latency |
| Web/research | tool calling and citation-compatible workflow | evidence quality, reliability, cost |
| High reasoning | reasoning support and sufficient output/context budget | quality, reliability, cost |

The classifier should begin as deterministic rules with observable features.
Learned routing is allowed only after trace quality, privacy, and offline replay
tests exist. OpenJarvis' `RoutingContext`, heuristic policy, trace, and energy
telemetry are useful references, but importing its full runtime would duplicate
Hermes.

## Selection algorithm

1. Honor an explicit user model/provider choice if it satisfies hard safety and
   capability constraints.
2. Filter candidates by enabled state, health, modality, context, tool protocol,
   provider allowlist, and data-boundary policy.
3. Score the remainder using the route profile and current cost/latency data.
4. Return a structured decision containing route, candidate, reasons, rejected
   candidates, estimated cost class, and fallback chain.
5. Pass the selected provider/model into Hermes through supported configuration
   or constructor surfaces.

## Fallback invariants

- Never silently cross a local-only or data-residency boundary.
- Never fall back to a model missing a required modality or tool protocol.
- Never silently escalate to a paid/high-cost tier when budget confirmation is
  required.
- Retry only transient failures. Authentication, policy denial, invalid model,
  and malformed request errors are surfaced rather than sprayed across
  providers.
- Bound attempts and avoid retrying the same failed provider/model pair.
- Preserve task/session correlation in logs while excluding prompts and keys by
  default.
- Hermes' own fallback remains the execution-level mechanism. JL Agent supplies
  a policy-compliant ordered chain rather than implementing a second retry loop.

## Apple-local route

OpenJarvis' `src/openjarvis/engine/apple_fm.py` proves a viable Apple Foundation
Models path on Apple Silicon/macOS 26+, but it requires Apple Intelligence,
`apple-fm-sdk`, and full Xcode. Phase 2 should evaluate it as an isolated Hermes
provider plugin or subprocess adapter; it is not a Phase 1 dependency and must
degrade cleanly when unavailable.

A non-normative configuration example is in `config/model-router.example.yaml`.
