# Security and permissions model

## Trust model

JL Agent is a single-user assistant, but its inputs are not automatically
trusted. Web content, files, messages, tool output, model output, MCP responses,
skills, plugins, and scheduled prompts may be adversarial. An LLM decision,
prompt instruction, string scanner, or approval regex is not a security
boundary.

The load-bearing boundary is OS/process isolation plus least-privilege macOS
permissions and credentials. Hermes' local terminal backend has the permissions
of the current user. Tasks that ingest untrusted content or require strong
containment must use a whole-process/container sandbox with explicit filesystem
and network policy. Sandboxing only the terminal does not contain in-process
plugins, skills, code execution, or MCP children.

## Action classes

| Class | Examples | Default | Explicit confirmation |
|---|---|---|---|
| Read-only | Read an allowed local file, inspect app/window metadata, search public web, list calendar items | Allow within granted scope; audit sensitive reads | Required when entering a newly sensitive scope, capturing protected screen content, or sending private data to a remote model |
| Reversible local | Create a new file, edit inside an approved workspace with recovery, open an app, change an undoable local setting | Allow only in scoped workspace with backup/undo semantics | Required if target is outside the active workspace, broad, ambiguous, or the change may disrupt another app/user workflow |
| Destructive | Delete/overwrite without recovery, terminate unrelated processes, force-push, erase history, disable security controls | Deny unattended; preview exact target and impact | Always required immediately before execution; approval is single-action, short-lived, and invalidated by argument/target changes |
| External communication | Send email/message, publish/post, submit form, create ticket visible to others | Drafting is reversible; transmission is not | Always required with recipient, channel, and final payload preview unless a narrowly scoped recurring automation was explicitly authorized |
| Credential-sensitive | Read/use/store/export API keys, OAuth grants, Keychain access, authentication changes | Secrets remain in OS credential storage or injected environment; redact logs | Required for initial connection, privilege/scope expansion, reveal/export, or use outside the originally approved service and purpose |
| Financial/high risk | Purchase, trade, transfer, subscription change, legal/medical decision execution, account recovery/security changes | No unattended execution; no model-only authorization | Always required with amount/asset/account/consequence preview and a separate trusted confirmation factor where supported |

Read-only is not synonymous with harmless: sending a private file to a cloud
model is both a read and an external disclosure, so the stricter class wins.
When classes overlap, apply the maximum confirmation and isolation requirement.

## Approval invariants

- The permission engine evaluates normalized tool name, arguments, resolved
  target, capability ID/version, caller, session, and current foreground app.
- Approval binds to that exact tuple and expires quickly. Retries with changed
  arguments require a new decision.
- Timeout, unavailable approval surface, malformed decision, and lost session
  all deny execution.
- A model, skill, plugin, cron job, or MCP server cannot approve its own action.
- Broad approvals such as “all deletes” or “all financial actions” are invalid.
- Pre-authorization for scheduled external messages must state destination,
  template/data scope, frequency, validity window, and revocation path. Any
  expansion requires confirmation.
- The final UI uses plain descriptions of effect, not raw command text alone.

## macOS permissions

Request OS permission only when the user enables a dependent capability:

- Microphone: wake word and voice capture.
- Speech recognition, when the selected provider/implementation needs it.
- Screen Recording: pixels/screenshots; window metadata alone must not trigger it.
- Accessibility/Input Monitoring: mouse, keyboard, global hotkeys, and UI tree
  control.
- Automation/Apple Events: per-target application automation.
- Notifications: approval and scheduled-task delivery.

The native app must show why a permission is needed, which capability will use
it, a direct System Settings path, current status, and how to revoke it. A denied
permission produces `blocked` health with remediation; it must not start a loop
of repeated system prompts.

## Computer-use safety

PersonalJarvis provides useful patterns in `jarvis/cu/target_guard.py`,
`jarvis/cu/ledger.py`, `jarvis/cu/verify.py`, and `jarvis/safety/tool_executor.py`.
JL Agent will adapt the concepts, not embed its whole runtime:

1. Capture the foreground app/window identity and geometry with the proposal.
2. Re-check target identity immediately before input; fail closed if focus moved.
3. Prefer accessibility elements over coordinates.
4. For coordinate actions, bind coordinates to the captured frame and reject
   stale geometry.
5. Show a visible input-control indicator and provide a hardware/user interrupt.
6. Verify observable postconditions after action and stop on ambiguity.
7. Keep an action ledger that suppresses accidental duplicate side effects.

## Credentials and data

- No secret may appear in Git, committed YAML, command-line arguments, logs,
  crash reports, capability descriptors, or model prompts unless the explicit
  task requires the value and its destination is authorized.
- Prefer Keychain for long-lived credentials and short-lived tokens for child
  processes. Pass only allowlisted environment variables.
- MCP servers, skills, and plugins receive no ambient credentials by default.
- Screen images and local files are processed locally unless the user/policy
  permits the selected remote provider. Redact sensitive regions or text before
  remote vision where practical.
- Audit records store action metadata, decision, actor, timestamp, and outcome;
  payloads are hashed or summarized when they may contain private data.

## External surfaces

Bind local IPC/HTTP to loopback or user-owned Unix sockets, authenticate every
caller, and use file permissions to restrict access. Network gateways require an
explicit sender allowlist. Session IDs are routing identifiers, not credentials.
Untrusted content never gains approval authority.

## Supply chain

- Pin upstream revision and package lock data; review diffs before upgrades.
- Verify source, license, release provenance, and transitive changes.
- Treat skills/plugins as code with agent-process privilege; review all scripts,
  hooks, and binaries, not only their manifest.
- Prefer isolated MCP/subprocess integration for externally maintained code.
- Preserve Apache-2.0 LICENSE/NOTICE and modified-file notices if OpenJarvis or
  PersonalJarvis code is later adapted.

## Logging and incident response

Logs are local, rotating, permission-restricted, and redacted. Security events
include denials, approvals, credential-scope changes, capability install/update,
external sends, destructive actions, financial attempts, and sandbox failures.
A user can disable a capability, revoke credentials, stop the runtime, and
export a sanitized audit summary. Raw secrets are never included in exports.

## Current implementation limitations

Phase 5 voice and wake are disabled by default and require both
`JL_AGENT_VOICE_ENABLED=1` and
`JL_AGENT_VOICE_ACTIVATION_APPROVED=1` before a start operation can reach
Hermes. The second flag is an operator assertion that the exact dependency/model
and macOS Microphone step was reviewed; deterministic tests never set it for a
real backend. Hermes remains responsible for capture, VAD, STT, wake detection,
TTS, stop phrases, and cleanup. JL binds the process-wide owner to one
authenticated caller/session and stores only bounded in-memory transcript/reply
events for that owner. Raw audio is neither exposed over IPC nor added to the
audit ledger. Every spoken turn passes an explicit empty toolset, so voice
cannot execute tools in this Phase. The native app does not request or own
Microphone TCC; the foreground Python runtime is the capture process.

Phase 4B retains the native Keychain-backed client credential, Keychain RSA consent
key, and a separate signature-authenticated consent socket. Normal IPC still
cannot issue or activate approvals. The runtime creates an opaque, bounded,
short-lived pending record after policy evaluation; only a signed approve or
reject decision may resolve it. Fingerprints, approval TTLs, and approval tokens
remain runtime-owned. Exact approval is consumed before preparation and the
Phase 3B gate still performs final identity, policy, health, route, fingerprint,
and replay checks.

The server-side bearer credential remains in its user-owned private runtime
file; the app imports it into Keychain and does not display or log it. The
native consent public key is enrolled through a user-owned private file and is
pinned when the foreground runtime starts. This protects the protocol boundary,
not arbitrary malicious code already executing as the same macOS user. Final
code-signing identity enforcement and stronger process isolation remain future
distribution work.

Approval, pending-consent, and prepared state remain memory-only and disappear
on restart. The service is serial on each endpoint and has no LaunchAgent or
supervisor. The native validation route and fake adapter make no provider,
network, or paid calls; a real Hermes tool execution still requires compatible
local Hermes/provider configuration. Hermes guardrails remain defense in depth,
not OS containment. Continue to use an explicitly trusted workspace and do not
enable unattended destructive, communication, credential, or financial
actions.

The pinned Hermes computer-use capability is now projected through JL rather
than reimplemented. On macOS, readiness requires its audited cua-driver 0.20+
contract plus Accessibility and Screen Recording for the CuaDriver identity;
Input Monitoring is not requested by this pin. Unknown, denied, unavailable, or
restart-pending required TCC state is not executable. Client-supplied health
cannot replace the runtime-owned probe.

The permission engine validates the exact inner computer-use action and its
minimum scope. Screen capture and all input enter protected scope; mutating
input cannot run unattended. Existing external communication, credential,
destructive, and financial classes remain stronger when combined with UI
control. Exact app target, resolved target, foreground identity, arguments,
caller/session, capability identity, and scopes are approval-bound. The gate
rechecks foreground identity immediately before input and denies unavailable or
changed context. Hermes' sticky target, stale element token, hard blocks,
approval, and effect-verification logic remain defense in depth.

The host now has official Cua Driver v0.28.0 installed as
`/Applications/CuaDriver.app`, signed as `com.trycua.driver` by team
`YCK386LBJ7`, with Accessibility and Screen Recording granted to that identity.
Phase 4C validated one read-only AX capture through authenticated JL policy,
exact native consent, the final execution gate, pinned Hermes, and CuaDriver.
That accepted proof was not repeated. Hardware interrupt UI, broad visual-state
verification, multi-step GUI planning, and production distribution lifecycle
remain out of scope.

Phase 4C additionally binds live readiness to the exact pinned Hermes checkout,
complete driver manifest, executable-derived CuaDriver app path, exact
`com.trycua.driver` bundle ID, allowlisted official signing team, both TCC
grants, authenticated runtime, policy availability, and current consent-key
enrollment. A raw executable outside the signed app, an ad-hoc/wrong-team app,
or an unavailable probe fails closed. Standard mode starts MCP on demand; JL
does not enable private unrestricted mode or approval bypass.

Consent enrollment status uses only a SHA-256 fingerprint of the public key.
The runtime pins the public key at startup and compares the current private
owned enrollment file to that pin; rotation or loss while running blocks live
computer use until an explicit restart. The private key remains Keychain-only.
The ad-hoc JL development build is a documented host limitation when no Apple
code-signing identity exists, but it is not granted CuaDriver's TCC access.
