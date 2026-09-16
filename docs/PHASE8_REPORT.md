# Phase 8 Report — Native Skills Manager

**Status:** Complete for the approved reduced scope  
**Date:** 2026-09-16 ICT

## Outcome

JL now has a minimal native SwiftUI Skills panel backed by the existing authenticated JL AF_UNIX contract. The panel lists Hermes-managed metadata, imports a local folder or root `SKILL.md`, shows bounded preview and scan results, and performs explicit Enable/Disable transitions.

No Hermes source was changed. Memory query-scoping remains deferred because the pinned Hermes build has no clean supported seam for changing built-in curated-memory injection without changing Hermes or duplicating its store/index.

## Implemented surface

- `src/jl_agent/control/skill_manager.py` composes the pinned Hermes validators, `SkillBundle`, quarantine, Skills Guard, managed install, disabled config, lock/provenance, and lazy `skill_view` surfaces.
- `src/jl_agent/control/request_state.py` routes six UI-only operations after existing credential authentication:
  `skills-list`, `skill-preview`, `skill-import`, `skill-scan`, `skill-enable`, and `skill-disable`.
- `src/jl_agent/runtime_service.py` composes the adapter without changing the existing prepare/execute/consent path.
- `macos-app/Sources/JLAgentCore/RuntimeClient.swift` and `Models.swift` provide typed, bounded native IPC methods and metadata/preview/scan models.
- `macos-app/Sources/JLAgentApp/AgentViewModel.swift` and `ContentView.swift` provide the panel and folder/file importer.
- `macos-app/Sources/JLAgentNativeTests/main.swift` covers native authenticated envelope and bound behavior.

The lifecycle is:

`user folder/SKILL.md -> bounded local read -> Hermes quarantine -> Hermes Skills Guard -> Hermes-managed install -> durable Disabled -> explicit Enable/Disable -> lazy Hermes preview/load`

Installed, enabled, and authorized remain separate states. Enable changes Hermes visibility only; existing JL policy, consent, execution, and audit gates remain authoritative for actions.

## Security and context guarantees

- Import reads regular files only and rejects traversal, absolute/unsafe bundle paths, symlinks, non-regular files, invalid frontmatter, excessive files, and oversized content.
- Import and scan execute no scripts/hooks, install no dependencies, make no network calls, load no credentials, and make no model/provider call.
- Hermes scan policy is authoritative; JL does not expose a force-install path.
- Disabled is persisted before the bundle enters Hermes active storage. Enable is explicit and audited.
- List and scan responses are metadata/findings bounded; list never includes a skill body. Preview is an explicit bounded read only.
- The operations are not model-facing tools. They are routed to the authenticated UI handler before the normal action decoder and are not added to `AssistantAdmission` or ordinary conversation context.
- A deterministic isolation test populated 100 installed metadata entries and confirmed the same normal assistant response before and after listing them.

## Bounded E2E

One harmless local `SKILL.md` was run through the authenticated handler seam using the real pinned Hermes gateway and a temporary Hermes home:

1. empty managed list
2. local import
3. Hermes quarantine, Skills Guard scan, and managed install
4. Disabled-by-default verification
5. bounded scan and preview
6. explicit Enable and verification
7. explicit Disable and verification

Result: `phase8-authenticated-hermes-e2e: OK`. The handler/action decoder was called zero times, the metadata list contained no body, and the audit contained the expected nine lifecycle events with local provenance and an import hash. The temporary fixture was removed after the run.

This was bounded at the authenticated JL handler seam. Direct AF_UNIX socket binding is unavailable in the current sandbox (`Operation not permitted`); no production workaround was made.

## Validation

- 9 focused Python Skill Manager tests: pass.
- 10 focused runtime/client/request-state regression tests: pass.
- `swiftc -parse` over all native Swift sources: pass.
- `git diff --check`: pass.
- Hermes pinned gateway smoke: pass (`file_limits = 50 files / 5120 KiB total / 256 KiB single file`).
- Native `swift build` was attempted but blocked by the host toolchain: CommandLineTools Swift SDK/compiler version mismatch, plus an unwritable global module cache. No dependencies were installed and no source change was made for this environment issue.
- No heavy Hermes suite, provider/model call, live skill execution, or UI automation was run.

## Preservation checks

- Hermes HEAD remains `044a77b3b6af4ce16138d42762f812a20b9f7a89`.
- `git apply --reverse --check docs/PHASE6_UPSTREAM.patch` passes inside Hermes.
- The existing Phase 6 upstream worktree patch remains untouched in `cron/scheduler.py`, `cron/scheduler_script.py`, and `cron/execution_policy.py`.

