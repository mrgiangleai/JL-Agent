# Phase 8 Handoff — Native Skills Manager

Phase 8 is complete for the approved reduced scope.

## Fresh-context entry point

Read `docs/PHASE8_PROPOSAL.md`, then this handoff and `docs/PHASE8_REPORT.md`. The implementation is a thin native/UI control layer over the authenticated JL runtime and pinned Hermes skill primitives. Do not add a second registry, loader, memory store, vector index, recommender, or model-facing skill tool.

## Contracts to preserve

- Built-in Hermes curated-memory query-scoping is deferred. Do not modify Hermes or duplicate `MemoryStore`.
- The only Phase 8 skill operations are `skills-list`, `skill-preview`, `skill-import`, `skill-scan`, `skill-enable`, and `skill-disable`.
- `installed != enabled != authorized`.
- Import is local, bounded, quarantine-first, Hermes-scanned, Hermes-installed, and Disabled by default.
- Import/scan/preview do not execute skill code, install dependencies, use network, load credentials, or call a model/provider.
- Normal JL assistant turns do not receive skill metadata or bodies. Skill manager traffic is authenticated UI IPC only.
- Existing JL policy, approval, consent, execution, stop, and audit gates remain unchanged.
- Do not modify the Hermes submodule. Preserve the Phase 6 patch.

## Code map

- Backend adapter: `src/jl_agent/control/skill_manager.py`
- Authenticated routing: `src/jl_agent/control/request_state.py`
- Runtime composition: `src/jl_agent/runtime_service.py`
- Native IPC/models: `macos-app/Sources/JLAgentCore/RuntimeClient.swift`, `Models.swift`
- Native view model/panel: `macos-app/Sources/JLAgentApp/AgentViewModel.swift`, `ContentView.swift`
- Native contract checks: `macos-app/Sources/JLAgentNativeTests/main.swift`
- Deterministic backend tests: `tests/test_skill_manager.py`

## Validation boundary

The bounded E2E passed through authenticated handler dispatch with the real pinned Hermes gateway. AF_UNIX bind was not available in the sandbox and was not worked around in production code. Native source parsing passed; native build remains host-toolchain-blocked until a matching Xcode/SDK toolchain is available.

The expected post-commit root state is clean for Phase 8 files. The Hermes submodule remains intentionally modified only by the pre-existing Phase 6 patch and therefore appears as a dirty submodule in root status.

## Deferred next work

Memory query-scoping, model-facing skill tools, automatic recommendations, remote marketplace work, skill editing/update/removal, and imported-skill behavioral execution/sandboxing remain out of scope.

