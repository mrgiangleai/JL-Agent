# Adapters

JL Agent-owned adapters translate stable JL contracts to upstream Hermes,
external providers, or platform services. An adapter may not import or patch an
unrelated Hermes internal module. Prefer Hermes plugin hooks, MCP, subprocesses,
or documented public entrypoints.

No runtime adapter is implemented in Phase 1.
