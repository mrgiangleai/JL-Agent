# Phase 8 Proposal — Memory + Skill Manager

**Status:** Phase 8 implementation complete; built-in memory query-scoping remains deferred
**Date:** 2026-09-16
**Scope:** Thin backend adapter plus minimal native SwiftUI manager; no Hermes changes, no dependency installation, no skill execution, no model-facing skill tools, and no heavy tests.

## Executive decision

Hermes already owns the durable session store, conversation search, memory tool, memory-provider hooks, skill format, skill discovery, lazy skill loading, skill metadata, enable/disable configuration, skill hub storage, scanning, and audit surfaces.

JL should not create a second memory store, vector index, skill format, skill loader, skill registry, or agent loop. Contract 1 is resolved as **deferred**: the pinned Hermes build has no clean supported seam that makes its built-in curated memory query-scoped without changing Hermes or duplicating retrieval. Contract 2 is resolved as a thin, authenticated Skill Manager/control adapter around Hermes' existing quarantine, scan, install, disabled-state, and lazy-load surfaces. No memory implementation is included in the reduced Phase 8 scope.

The native app remains a UI and IPC client; Hermes remains authoritative for skill content and loading, while existing JL permissions, risk policy, consent, and execution gates remain authoritative for actions.

## Implementation status

Implemented in JL:

- `src/jl_agent/control/skill_manager.py`: authenticated-handler target with a pinned-Hermes gateway, bounded metadata/preview/scan, local-folder import, quarantine, scan policy, Hermes-managed install, disabled-first ordering, enable/disable, and audit projection.
- `src/jl_agent/control/request_state.py` and `src/jl_agent/runtime_service.py`: route the six Skill Manager operations through the existing authenticated AF_UNIX boundary.
- `tests/test_skill_manager.py`: focused deterministic lifecycle and isolation coverage.
- `macos-app/Sources/JLAgentCore/RuntimeClient.swift` and `macos-app/Sources/JLAgentCore/Models.swift`: typed authenticated native IPC operations and bounded metadata/preview/scan models.
- `macos-app/Sources/JLAgentApp/AgentViewModel.swift` and `macos-app/Sources/JLAgentApp/ContentView.swift`: minimal native Skills panel with import, list, preview, scan, enable, and disable controls.
- `macos-app/Sources/JLAgentNativeTests/main.swift`: focused native IPC envelope and bound checks.

Not implemented: model-facing skill tools, automatic skill recommendation, built-in curated-memory query scoping, or any Hermes modification.

The two resolved contracts are:

1. **Curated memory:** keep Hermes' current built-in behavior unchanged for now. Reuse `session_search` for explicit conversation recall and any configured external `MemoryProvider` for query-scoped recall. Do not patch `_memory_parts`, monkey-patch agent state, re-read the files through a JL-owned index, or add a second provider/store solely to emulate built-in memory.
2. **Imported skills:** `user folder/SKILL.md -> Hermes quarantine -> Hermes scan/policy -> Hermes-managed install -> durable Disabled state -> explicit Enable -> lazy Hermes loading`, with no execution during import and no permission grant from Enable.

## Hermes audit

### Memory and conversation state

Hermes provides:

- Durable `AIAgent` transcripts in a profile-scoped SQLite session database through `agent/session_persistence.py` and `hermes_state.py`.
- Session creation, resume, lineage, compression, metadata, and deletion through `hermes_state_sessions.py`.
- FTS5/trigram conversation search and recent-message queries through `hermes_state_search.py`.
- The `session_search` tool (`tools/session_search_tool.py`) with discovery, scroll, read, and browse modes. It performs database retrieval without an LLM call and hydrates real messages.
- Prompt guidance to use `session_search` when a past conversation may be relevant. Compaction also preserves pointers for later recovery.

This is sufficient for conversation/session memory. JL should call or adapt these existing surfaces rather than maintain a parallel transcript or search database.

### Long-term memory

Hermes provides a bounded file-backed memory store:

- `tools/memory_tool.py` manages curated `MEMORY.md` notes and `USER.md` profile facts under the profile-scoped memories directory.
- `tools/memory_tool_store.py` provides bounded files, atomic writes, file locking, drift protection, threat-pattern checks, batch operations, and frozen prompt snapshots.
- The `memory` tool supports add, replace, remove, and batch operations with the existing write-approval path.

The important current behavior is also the primary gap: `agent/system_prompt.py` adds the built-in memory snapshot to every session system prompt. Hermes documents this as bounded curated memory “injected into the system prompt every session.” The bounded size helps, but it is still global injection rather than relevance retrieval.

Hermes also has an external `MemoryProvider` interface (`agent/memory_provider.py` and `agent/memory_manager.py`) with `prefetch(query, session_id)`, queued prefetch, turn synchronization, pre-compression hooks, timeouts, sanitization, and a dynamic `<memory-context>` block. `agent/turn_context.py` appends that dynamic block only to the current user API content, preserving the static prompt prefix.

### Resolved memory contract: defer built-in query scoping

The source audit proves that no clean supported seam exists in the pinned build:

- `agent/agent_init.py::_init_memory` constructs `tools.memory_tool_store.MemoryStore` directly and calls `load_from_disk()`.
- `agent/system_prompt.py::_memory_parts` directly calls `MemoryStore.format_for_system_prompt("memory")` and `format_for_system_prompt("user")` whenever the built-in flags are enabled. Those are frozen, whole-target prompt blocks.
- `MemoryStore` exposes persistence and `format_for_system_prompt`; it does not expose a relevance query API or implement `agent.memory_provider.MemoryProvider`.
- `agent.memory_provider.MemoryProvider` and `agent.memory_manager.MemoryManager` are clean extension points for one external provider, but they do not adapt the built-in `MemoryStore` or suppress `_memory_parts`.
- Turning off the built-in flags removes the built-in memory surface; it does not provide query-scoped access to the same curated entries.

The remaining ways to force the desired behavior without a Hermes change would be unsupported private-state manipulation or a JL/provider implementation that independently reads, parses, and ranks `MEMORY.md`/`USER.md`. Both violate the reuse-first boundary. **Decision: defer query-scoped retrieval of built-in curated memory.** Phase 8 must not claim that behavior is implemented.

The reusable memory contract is therefore limited to `session_search` (`tools/session_search_tool.py`) for on-demand conversation recall and the existing external-provider path: `MemoryProvider.prefetch`, `MemoryManager.prefetch_all`, `is_trivial_prompt`, `build_memory_context_block`, and `turn_context.compose_user_api_content`. A future Hermes-supported built-in query API may reopen this decision.

### Skills

Hermes provides the complete skill lifecycle needed by the core:

- `tools/skills_tool.py` defines the `SKILL.md` format and optional `references/`, `templates/`, `assets/`, and `scripts/` files.
- `skills_list` returns compact metadata; `skill_view` resolves a skill safely and loads `SKILL.md` or a requested linked file only when explicitly needed.
- `agent/skill_utils.py` handles frontmatter, platform/environment conditions, disabled skills, external directories, precedence, collision handling, and project-skill quarantine.
- `agent/skill_commands.py` supports lazy slash-command loading and bounded stacking of skills.
- `agent/prompt_builder.py` builds a cached metadata index and does not put full skill bodies in the prompt.
- `tools/skills_guard.py` scans bundles and detects path escapes, symlinks, binaries, executable files, oversized bundles, suspicious text, prompt-injection patterns, exfiltration patterns, persistence/config changes, and invisible Unicode. The scanner produces safe/caution/dangerous policy results.
- `hermes_cli/skills_hub.py` and `tools/skills_hub_install.py` provide quarantine, scan, trust policy, installation, provenance/lock records, hashes, audit events, update, and uninstall for hub sources.
- `hermes_cli/skills_config.py` and the dashboard API support enable/disable through the profile disabled-skill configuration.
- `tools/skill_usage.py` tracks provenance and usage metadata.
- The existing Hermes dashboard/desktop API already exposes list, content, toggle, scan, hub preview/install, update, and uninstall operations.

Hermes is therefore already sufficient for normal skill discovery and lazy content loading. JL mainly needs a native management surface and a safe local-import path that ends at Hermes-managed storage/configuration. The local-folder import is an adapter composition because Hermes' existing `do_install` resolves remote identifiers; JL must not turn it into a new installer or loader.

### Resolved skill import contract

The exact lifecycle is:

`installed != enabled`: installation creates a managed, disabled artifact. `enabled != authorized`: Enable changes Hermes discovery visibility only and never authorizes a capability or action.

1. **Select:** native UI sends one authenticated user-selected folder or `SKILL.md` path over the existing JL AF_UNIX protocol. The adapter reads regular files only; it performs no imports, subprocesses, network calls, hooks, package operations, or script execution.
2. **Normalize:** the adapter creates Hermes' `tools.skills_hub_models.SkillBundle` from the selected files. It reuses the bundle path/name validators in `tools.skills_hub_models` and Hermes frontmatter/skill metadata parsing. Missing or invalid `SKILL.md`, name mismatch, traversal, absolute paths, symlinks/junctions, unsupported files, and size/count limits are rejected.
3. **Quarantine:** call `tools.skills_hub_install.quarantine_bundle(bundle)`. The quarantine directory is the only write target until scan approval.
4. **Scan:** call `tools.skills_guard.scan_skill_cached(quarantine_path, source="community", source_url="local-folder", cache_dir=<Hermes hub scan cache>)`, then `should_allow_install(result, force=False)`. JL never exposes `force`; community caution/danger results do not proceed.
5. **Install:** before moving the bundle into active storage, durably add the canonical skill name to the profile disabled set with `hermes_cli.skills_config.get_disabled_skills` and `save_disabled_skills`. Then call `tools.skills_hub_install.install_from_quarantine(...)` with the scan result/provenance and a fixed managed category such as `imported`. If install fails, leave the disabled marker in place.
6. **Record:** rely on Hermes' `HubLockFile.record_install` path used by `install_from_quarantine` for hash, source, identifier, scan verdict, files, metadata, and audit/provenance. The JL projection must label `source="local-folder"` from the lock metadata rather than assume every lock entry is a remote hub item.
7. **Enable:** only an explicit authenticated `skill-enable` request may remove the disabled name through the same Hermes config functions, after JL authorization, health, and state checks. Enable changes discovery visibility only; it grants no tool, credential, network, or execution authority.
8. **Load:** an enabled skill is loaded only through Hermes `tools.skills_tool.skills_list` metadata or `skill_view` content/linked-file loading. Import and Enable never call `skill_view`, execute a script, or preload a body.

The adapter must reject name/path collisions rather than pass a replacement/force option. The existing `hermes_cli.skills_hub.do_install` is not the local import entry point because it fetches/resolves remote sources. Existing dashboard routes (`/api/skills`, `/api/skills/toggle`, and content/scan routes) are reference behavior, not a direct native-app bypass of JL IPC.

## Actual gaps and boundary decisions

| Area | Hermes today | Phase 8 decision |
|---|---|---|
| Session memory | Durable SQLite sessions plus `session_search` | Reuse directly; no JL transcript store |
| Curated long-term memory | Bounded `MemoryStore`, globally injected snapshot | **Defer query scoping**; no Hermes change, no second store/index |
| Dynamic recall | External `MemoryProvider.prefetch` and sanitized turn-local context | Reuse only for configured external providers; cap results and skip trivial prompts |
| Skill format/loading | `SKILL.md`, `skills_list`, `skill_view`, linked-file loading | Reuse unchanged |
| Discovery | Filesystem scan, metadata cache, platform/disabled filtering | Project metadata through JL; do not duplicate scan/load logic |
| Remote install | Hub install with quarantine, scanning, lock/provenance, audit | Leave existing hub lifecycle authoritative; expose only through a JL-gated adapter |
| Local folder import | No general user-selected downloaded-folder import | Compose `SkillBundle` + `quarantine_bundle` + Hermes scan/install primitives |
| Default activation | Enable/disable config exists, but install does not default to disabled | Persist disabled name **before** `install_from_quarantine` moves files into active storage |
| Suggestions | Metadata and conditions exist, but no JL suggestion projection | Optional bounded metadata-only top-N suggestion; loading requires explicit acceptance |
| Prompt growth | Cached all-installed name/description index is included when skills tools are active | Reduced Phase 8 does not expose Hermes skill tools in ordinary JL model turns, so `_skills_prompt` stays empty through Hermes' existing gate. Manager listing is IPC/UI-only; re-enabling model-facing skills is deferred until a bounded supported seam exists |
| Security authority | Hermes scanner/policy and agent-process execution | JL gates remain mandatory; skill enablement never grants a capability |

## Minimal architecture

```text
SwiftUI Skill Manager
        |
        | authenticated versioned AF_UNIX IPC
        v
JL SkillManagerAdapter / control layer
        |-- validates request, profile, path, state transition, and JL policy
        |-- stages and quarantines local imports
        |-- composes Hermes SkillBundle/quarantine/scan/install/config primitives
        |-- does not own a skill store, scanner, loader, registry, or recommender
        v
Pinned Hermes skill/memory surfaces
        |-- skills_list / skill_view / session_search
        |-- SkillBundle / quarantine_bundle / scan_skill_cached / should_allow_install
        |-- install_from_quarantine / skills_config / HubLockFile
        v
Existing JL permission, risk, consent, execution, and audit gates
```

The native app must not import Hermes Python modules, implement tool execution, or call dashboard internals directly. It should use the existing authenticated JL IPC boundary. The adapter may project Hermes REST/CLI behavior internally only where that is a supported and auditable seam; direct private-internal coupling should be treated as an integration risk. The local-folder composition necessarily depends on the pinned hub primitives and their validators; if those private helper functions cannot be safely wrapped without Hermes changes, local import is deferred rather than reimplemented.

### Minimal JL adapter surface

The adapter is the only new JL control surface. It is hosted by the existing `src/jl_agent/runtime_service.py` and `src/jl_agent/control/ipc.py` boundary, and uses the existing `control/auth.py`, `control/codec.py`, `control/health.py`, `control/permissions.py`, `control/execution.py`, and `control/audit.py` contracts. It does not create a second approval or execution path.

Proposed operations are intentionally small and are IPC/UI operations, not model tools:

- `skills-list`: metadata, provenance, enabled/disabled state, scan verdict, hash, and readiness only; it is returned to the native manager and never injected into a model turn.
- `skill-preview`: frontmatter and bounded content preview; full content remains an explicit Hermes `skill_view` operation.
- `skill-import`: a user-selected local folder or `SKILL.md`, following the exact lifecycle above.
- `skill-scan`: re-run Hermes validation for a managed skill without loading or executing it.
- `skill-enable` / `skill-disable`: explicit state changes routed through JL policy and Hermes `skills_config`; Enable is not authorization.

Automatic per-turn skill suggestion is outside this reduced contract. If it is revisited, it may only be a fixed-size metadata-only UI result; it may not use embeddings, a vector DB, an LLM recommender, a skill body, activation, or execution.

Deletion, remote installation, update, and editing should remain outside the smallest Phase 8 scope unless an existing Hermes surface can be exposed without widening JL authority.

## Memory and context/token strategy

1. Keep session transcripts and curated memory on Hermes-managed disk. Do not copy them into JL-owned storage.
2. Use `session_search` for past-conversation recall only when the current turn references or plausibly needs prior context.
3. Do not claim query-scoped built-in curated memory. Until Hermes exposes a supported query API for `MemoryStore`, the built-in snapshot behavior remains unchanged and this contract is deferred.
4. For a configured external provider only, reuse Hermes’ `is_trivial_prompt`, `MemoryProvider.prefetch`, `MemoryManager.prefetch_all`, timeout, sanitization, `build_memory_context_block`, and `compose_user_api_content`. No embeddings/vector DB/LLM recommender is introduced by JL.
5. Keep skill bodies and linked files lazy. Load through Hermes `skill_view` only after an explicit user request or a bounded metadata suggestion is accepted.
6. For reduced Phase 8, do not expose `skills_list`, `skill_view`, or `skill_manage` as model-facing tools in ordinary JL turns. This makes Hermes' existing `_skills_prompt` gate produce no skill catalog without modifying Hermes. The native manager may invoke Hermes list/preview/load through the authenticated adapter on demand.
7. No full skill body is added to a per-turn context unless a later, explicit user-approved load contract permits it. Re-enabling model-facing skill tools is deferred until Hermes provides a bounded catalog configuration seam; JL must not patch the prompt builder.

## Security model for imported skills

Imported skill content is untrusted code/instructions. Hermes’ scanner is useful validation, not a proof of safety, and model/scanner output is never the security boundary.

The local import flow must:

- Accept only an authenticated, explicit user-selected source path; treat the path and every file beneath it as untrusted.
- Require a valid `SKILL.md` and parse strict frontmatter. Reject path traversal, absolute paths, symlink/junction escapes, unsupported layout, excessive file count/size, malformed metadata, and missing required provenance fields.
- Stage inside a project/profile-local quarantine directory, scan with Hermes Skills Guard, and record verdict, source path label, license, provenance, content hash, and timestamp before installation.
- Never execute scripts, install packages, fetch URLs, run hooks, or load credentials during import or scan.
- Store imported skills in Hermes-managed profile storage and persist them disabled by default. A safe scan does not imply activation.
- Keep imported files out of active Hermes discovery until the disabled state is durably recorded.
- Treat `requires_toolsets`, `requires_tools`, environment variables, credential files, and other skill metadata as readiness/visibility information only. They must not grant capabilities or ambient credentials.
- Require an explicit JL-gated enable request. Enabling a skill may change visibility only; every resulting tool/action still passes the existing JL authorization, risk, consent, execution, and audit path. A skill cannot approve itself.
- Preserve audit events for import, scan, enable, disable, update, and removal, without logging secrets or raw sensitive content.

If behavioral testing of an imported skill is ever required, it needs a separate process/OS isolation decision. It is not part of the smallest Phase 8 scope.

## Smallest Phase 8 scope

Phase 8 implementation should be limited to:

1. Record the negative memory finding as a versioned deferred contract: reuse `session_search` and configured external `MemoryProvider` surfaces only; do not alter built-in curated-memory injection.
2. A thin authenticated Skill Manager adapter and native control contract for list, preview, scan, local import, enable, and disable.
3. The exact local import lifecycle using `SkillBundle`, `quarantine_bundle`, `scan_skill_cached`, `should_allow_install`, `install_from_quarantine`, Hermes config disabled state, and `HubLockFile` provenance/audit.
4. A fixed context budget and no model-facing installed-skill catalog in ordinary JL turns. Automatic skill recommendation is deferred.
5. Focused deterministic validation of the adapter, quarantine, state transitions, gate preservation, and context bounds. No full Hermes suite, live provider calls, or live imported-script execution.

Explicitly deferred: changing built-in curated memory from global injection to query-scoped retrieval, a new semantic memory engine, a new skill registry/format/loader, remote marketplace work, automatic skill execution, imported-script sandboxing, skill editing, automatic skill recommendation, re-enabling model-facing skill tools with an unbounded catalog, changing Hermes' existing all-skill metadata prompt, and any modification of the Hermes submodule.

## Definition of Done

Phase 8 can close only when all of the following are demonstrated:

- Hermes remains pinned and its working tree is unchanged by Phase 8.
- Session memory, long-term memory storage, skill format, scanning, loading, and managed storage are still Hermes-owned.
- The deferred built-in-memory contract is not represented as implemented. Hermes' current built-in injection remains unchanged; only existing on-demand `session_search` and configured external-provider recall use query-scoped behavior.
- Full skill bodies and linked files are loaded lazily through Hermes only from explicit manager/user requests. Ordinary JL turns expose no Hermes skill tools and therefore receive no installed-skill catalog or skill body; installed-skill count does not increase their per-turn model context.
- A user-selected downloaded folder can be imported through authenticated JL IPC, validated in quarantine, recorded with provenance/hash, and remains disabled by default.
- Dangerous or structurally invalid imports are rejected or quarantined; no import path executes scripts, installs dependencies, fetches network content, or receives ambient credentials.
- Enable/disable requests are explicit, audited, and cannot bypass JL permission, consent, risk, execution, or stop gates.
- Any suggestion is metadata-only, bounded, non-authoritative, and cannot activate or execute a skill without explicit user action.
- Native SwiftUI communicates only through the existing JL IPC boundary; it does not import Hermes internals or implement a parallel engine.
- Focused deterministic checks pass, generated artifacts are cleaned, and no heavy/live validation is claimed as part of this discovery.

## Discovery evidence

The audit was source-only against the pinned Hermes submodule at commit `044a77b3b6af4ce16138d42762f812a20b9f7a89` (version 0.21.2), plus the JL documents listed in the request. Phase 7 remains closed. No files under Hermes were modified by this discovery, no dependencies were installed, and no heavy tests were run.
