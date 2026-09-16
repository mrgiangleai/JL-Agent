# Phase 7 proposal - Unified JL Assistant Loop

Date: 2026-09-16 ICT

## Scope

Status: approved for the first typed backend slice. Voice-path unification,
native UI and Hermes changes remain deferred.

Goal: one spoken or typed natural request enters JL, and JL admits it as one of:

- conversation;
- computer action;
- local reminder scheduling.

The implementation must reuse the existing voice, Hermes, computer-use,
permission, exact-consent and scheduler boundaries. It must not create a second
agent loop, router, scheduler, computer-use engine, approval system, or Hermes
fork.

For the approved first slice, `assistant-request` accepts typed input only.
The existing Phase 5 voice path remains unchanged.

## Existing architecture audit

### Runtime and IPC

`src/jl_agent/runtime_service.py` composes one foreground AF_UNIX runtime with:

- `SecureControlRequestHandler` for authenticated normal IPC;
- `TrustedConsentRequestHandler` for signed native consent;
- `JLControlPlane`, `PermissionRiskEngine`, `ExecutionGate` and
  `HermesExecutionAdapter` for exact action preparation and execution;
- `VoiceCoordinator` for voice/wake ownership;
- `AutomationManager` and `AutomationRuntime` for Phase 6 reminder management
  and scheduler authorization.

The SwiftUI app is only a control/consent client. It stores credentials and
consent signing keys in Keychain and does not import Hermes, capture audio, own
computer-use, or execute tools directly.

### Conversation and voice

`VoiceCoordinator` delegates capture, VAD, STT, wake detection, TTS and cleanup
to pinned Hermes. JL owns enablement, activation, caller/session ownership and
bounded events.

The current spoken turn path uses `HermesTextOnlyTurnRunner`, which calls
Hermes oneshot with:

```text
toolsets=[]
use_config_toolsets=False
```

That is the correct safety baseline for conversation. It also means current
voice cannot perform actions or schedules. Phase 7 should preserve tool-free
voice-to-Hermes conversation unless the new JL assistant admission layer
explicitly classifies and hands off an exact action or schedule through existing
JL gates.

### Computer-use

`core.hermes.computer-use` is projected through JL policy and the Phase 3B
execution gate. Hermes still owns the computer-use toolset, Cua backend,
snapshot/input logic, sticky target behavior and upstream guardrails.

JL already adds the load-bearing controls:

- runtime-owned CuaDriver readiness and TCC status;
- exact action scope derivation;
- signed native consent for protected reads/input;
- foreground app recheck before mutating input;
- dispatch through Hermes `model_tools.handle_function_call`.

Phase 7 should produce the same `ControlRequest` shape used by `prepare` and
`execute`. It must not call the Cua driver, Hermes tool handlers, or
computer-use internals directly.

### Automation

Phase 6 already wraps Hermes cron without adding a second scheduler:

- Hermes owns job storage, schedules, claims, due selection, execution, output
  and history.
- JL owns exact grants, signed activation, replay receipts, revoke and global
  stop.
- Jobs are fixed local `no_agent` reminders only.
- Jobs are created paused and require exact signed activation.
- The scheduler service is explicit foreground lifecycle only.

The Hermes submodule intentionally carries the reviewed three-file Phase 6
authorization hook patch. Phase 7 must not touch those Hermes files. Any future
automation expansion starts by re-auditing that hook.

### Gap

There is no current JL operation that accepts one natural request and decides
between conversation, action and scheduling. Existing native/runtime operations
are already split:

- `voice-*` and `wake-*`;
- `prepare` / `execute`;
- `automation-*`;
- `status` / `activity`.

The smallest Phase 7 change is therefore not a new agent. It is a new JL
admission layer that turns natural input into one of the existing surfaces, with
fail-closed clarification when the request is ambiguous.

## Proposed Phase 7 design

### New JL-owned admission operation

Add one authenticated normal-IPC operation:

```text
assistant-request
```

Payload:

```json
{
  "text": "open Notes and click the search field",
  "input_mode": "typed|voice",
  "foreground_app": "optional observed bundle/name",
  "timezone": "Asia/Ho_Chi_Minh"
}
```

Response states:

- `conversation_completed`;
- `action_prepared`;
- `awaiting_approval`;
- `action_completed`;
- `schedule_created_paused`;
- `schedule_awaiting_activation`;
- `clarification_required`;
- `denied`;
- `failed`.

This operation should live inside the existing authenticated socket and reuse
the same caller/session identity, request ID, size limits, lifecycle style and
error envelope. Consent decisions remain on the existing consent socket.

### Assistant admission component

Add a small JL component, tentatively:

```text
src/jl_agent/control/assistant_loop.py
```

The name describes product behavior, but the component must not become a
second runtime loop. Its job is admission and delegation only:

1. Normalize bounded text and context.
2. Classify intent with deterministic rules.
3. For conversation, call the existing Hermes text-only runner.
4. For computer action, build a normal `ControlRequest` and call the existing
   prepare/execute path.
5. For scheduling, call existing `AutomationManager.create()` and, if requested
   by the UI flow, existing activation challenge handling.
6. Return structured state for the native app.

Do not add model-based autonomous planning in the first slice. A model may be
used later to suggest a structured draft only after offline replay tests and
strict schema validation exist; even then, policy and execution remain JL's
existing gates.

### Intent classes

Start with deterministic, conservative classes:

#### Conversation

Default when no scheduling or computer-action trigger is confidently detected.

Execution path:

```text
assistant-request -> HermesTextOnlyTurnRunner -> Hermes oneshot with toolsets=[]
```

No tool execution. No scheduler. No computer-use. This keeps voice and typed
chat equally safe in the first Phase 7 slice.

#### Scheduling

Only accept fixed local reminders that fit Phase 6:

- "remind me ... in 10 minutes";
- "remind me ... at 9:00 tomorrow";
- "every day at 8:00 remind me ...";
- "every 2 hours remind me ...".

Execution path:

```text
assistant-request -> AutomationManager.create(paused=True)
```

The result is paused by default. Activation remains the existing exact signed
automation consent flow. No email, messaging, monitor, provider, MCP, browser,
file action, arbitrary script or autonomous scheduled agent job enters Phase 7.

If parsing cannot produce a bounded reminder name, note and Hermes-compatible
schedule string, return `clarification_required`.

#### Computer action

Only accept explicit, single-step desktop requests in the first slice. Examples:

- "capture the current screen";
- "list windows";
- "click the OK button in Notes";
- "type hello in TextEdit".

Execution path:

```text
assistant-request -> ControlRequest(core.hermes.computer-use)
                  -> existing prepare/consent/execute
                  -> HermesExecutionAdapter
                  -> Hermes computer_use tool
```

Mutating actions must include an exact app target and observed foreground
context, then pass the existing native consent and foreground recheck. If a
request implies multi-step GUI planning, broad desktop autonomy, hidden target
selection, credential entry, external send, purchase, deletion or financial
action, return `clarification_required` or `denied` according to current policy.

### Native app changes

Add one "Ask JL" input surface above the existing specialist panels.

The native app should:

- send text via `assistant-request`;
- continue to show runtime/computer-use/voice/automation status from existing
  endpoints;
- show existing consent sheets when the runtime returns a consent challenge;
- show "created paused" for schedule creation before activation;
- never locally classify or execute the request.

Voice should feed transcripts to the same assistant admission component instead
of directly calling the old tool-free conversation runner. The component will
still choose tool-free conversation by default, so this is a unification of the
entrypoint rather than enabling unrestricted voice tools.

## Smallest safe implementation plan

### Step 1 - Contract and tests first

Add tests for the new assistant operation with deterministic fakes:

- unauthenticated requests fail before classification;
- malformed or oversized natural input fails closed;
- plain chat calls the tool-free conversation runner;
- reminder text creates exactly one paused fixed local reminder;
- schedule activation still requires existing signed automation consent;
- computer-use capture/list maps to existing `prepare`;
- computer-use input action requires exact app and consent;
- ambiguous/multi-step/high-risk requests return clarification or denial;
- no path calls Hermes scheduler ticks, Cua driver, or Hermes tool handlers
  directly.

### Step 2 - Implement assistant admission in JL only

Add `AssistantRequestHandler` and `AssistantAdmission` around existing
dependencies:

- `turn_runner: Callable[[str], str]`;
- `control_handler` or direct existing `JLControlPlane`/`ExecutionGate`
  integration;
- `automation_manager`;
- `status/readiness` access for computer-use and automation availability.

Keep deterministic parsing intentionally small. Prefer clarification over
guessing.

### Step 3 - Wire runtime composition

Extend `SecureControlRequestHandler` with an optional `assistant_handler`, like
the existing `voice_handler` and `automation_handler`.

Wire it in `build_runtime_service()` using existing objects:

- same `VoiceCoordinator` turn runner dependency can point to assistant
  admission;
- same `AutomationManager`;
- same control plane, approvals, consent coordinator, execution gate and audit.

No Hermes submodule changes.

### Step 4 - Native client and UI

Add `JLRuntimeClient.assistantRequest(...)` and a compact Ask JL UI. Keep the
existing panels for inspection and manual control.

The UI should show exact runtime-returned states rather than reinterpreting
them locally.

### Step 5 - Validation

Run focused deterministic validation first:

```bash
.venv/bin/python -B -m unittest tests/test_assistant_loop.py tests/test_voice_ipc.py tests/test_automation_management.py tests/test_execution.py
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ty check src tests
cd macos-app && swift run JLAgentNativeTests && swift build
cd ..
git diff --check
git -C upstream/hermes-agent apply --reverse --check ../../docs/PHASE6_UPSTREAM.patch
shasum -a 256 upstream/hermes-agent/cron/execution_policy.py \
  upstream/hermes-agent/cron/scheduler.py \
  upstream/hermes-agent/cron/scheduler_script.py
```

Do not rerun Phase 5 voice live smoke, Phase 4C computer-use live smoke, or
Phase 6 scheduler live smoke unless a separate bounded diagnostic is explicitly
approved.

## Explicit non-goals

- No Hermes modification.
- No Hermes pin update.
- No second scheduler.
- No second agent loop or provider fallback layer.
- No second computer-use engine.
- No LaunchAgent, supervisor, global resume or automatic retry.
- No autonomous scheduled agent jobs.
- No external messaging, browser/network monitor, provider/MCP jobs, file
  actions or arbitrary script scheduling.
- No TCC bypass, automated permission mutation, or production microphone
  entitlement change.
- No model-based action planner in the first slice.

## Review questions

1. Should Phase 7 first ship typed `assistant-request` only, then switch voice
   transcripts to it after deterministic validation?
2. Should the first scheduling parser accept only relative schedules such as
   `in 10m` / `every 2h`, or also absolute natural dates like `tomorrow at 9`?
3. Should computer-use Phase 7 be read-only first (`capture`, `list_apps`,
   `list_windows`) before input actions?

## Proposed approval boundary

If approved, implementation should start with Step 1 tests and the new JL
admission component only. Stop again before any Hermes upgrade, Phase 6
automation widening, live voice/computer-use/scheduler smoke, or production
signing/TCC work.
