# Phase 3B report

## Outcome

Phase 3B extends the Phase 3A inert preparation boundary into a minimal,
auditable, policy-gated Hermes execution boundary. Hermes Agent remains the
only runtime core and is unchanged at revision
`044a77b3b6af4ce16138d42762f812a20b9f7a89` (`0.21.2`). Tests use deterministic
fakes and make no provider, network, local-model, or paid call.

## Selected Hermes execution surface

The production bridge constructs pinned `run_agent.AIAgent` using JL's exact
selected provider/model, ordered fallback provider/model entries, one enabled
toolset, session identity, and noninteractive runtime settings. It dispatches
the already selected tool and canonical argument object through
`agent.agent_runtime_helpers.invoke_tool(agent, tool, arguments, task_id)`.

This is the smallest surface that preserves Hermes' tool request/execution
middleware, plugin pre-tool hook, guardrails, enabled-tool validation, registry
dispatch, and native tool implementation without starting a second agent loop.
The surface is supported by Hermes' own `AIAgent._invoke_tool` forwarder and
internal execution path at the exact pin. It is treated as pin-coupled: every
Hermes upgrade must re-audit its signature and behavior.

JL passes only provider/model constraints, the bounded fallback chain, one
projected Hermes toolset, exact action and canonical arguments, session ID, and
request ID. Hermes continues to own credentials/provider resolution, fallback
implementation, middleware, safety checks, tool registry and implementations,
sessions/memory, and its agent runtime. JL must never duplicate those systems
or weaken a Hermes/upstream denial. `run_agent.py` was not patched.

## Adapter and final execution gate

`control/execution_adapter.py` translates a `HermesInvocationProjection` into
a typed runtime request and normalizes completed, denied, and failed results.
Its execution method accepts only an internal authority-bearing command issued
by the gate; callers cannot submit arbitrary raw adapter requests.

`control/execution.py` keeps a bounded set of prepared records in memory for at
most 30 seconds and consumes each lifecycle once. Immediately before dispatch it
revalidates:

- authenticated request authority and exact caller/session;
- capability identity/version, enablement, and required health;
- the authoritative Phase 2 action fingerprint;
- an exact already-consumed approval where confirmation was required;
- current upstream policy denial state;
- selected provider/model and fallback route against fresh routing constraints;
- exact action, canonical arguments, toolset, and invocation projection.

Any expiry, mutation, missing preparation, approval mismatch, route drift,
upstream denial, or duplicate execution fails closed. No second fingerprint or
wildcard authorization is created.

## Lifecycle

The Phase 3A state machine now permits only:

```text
prepared -> executing -> completed
                    +--> denied
                    +--> failed
```

`denied`, `failed`, and `completed` are terminal. Direct
`received -> executing` is illegal. Execution results carry request, caller,
and session identity, and the IPC response remains bound to the request ID.

## Audit strategy

`control/audit.py` appends JSONL events to a current-user-owned `0600` file in
a `0700` runtime directory. It records an allowlisted metadata schema:
timestamp, request/caller/session, capability identity/version, action class,
policy outcome, fingerprint and hashed approval references, consumed state,
provider/model, normalized error, and optional duration.

It does not accept arbitrary metadata and never records credentials, API keys,
auth tokens, raw approval tokens, full action arguments, user content, or model
reasoning. Retention is bounded by both event count and bytes; trimming uses a
private atomic replacement.

## Runtime service lifecycle

`runtime_service.py` composes the existing Phase 3A AF_UNIX transport,
authentication, strict production codec, approval store, control plane,
execution gate, Hermes adapter, and ledger. Default state lives under
`~/Library/Application Support/JL Agent/runtime` with predictable socket,
credential, audit, and readiness paths. Startup safely recovers only private
owned stale state, emits a private readiness file, and shutdown removes only
the identities created by that service instance.

The service runs in the foreground, exposes no TCP port, does not daemonize or
install a LaunchAgent, and exposes no IPC operation for trusted approval
issuance/activation.

## Validation and adversarial coverage

The 74 JL-owned tests cover the safe fake-Hermes path and fail-closed behavior
for authentication, malformed/unsupported IPC, identity mismatch, disabled or
unhealthy capability, denied policy, missing/consumed/replayed approval,
modified action/arguments/version, missing or expired-equivalent preparation,
route drift, upstream/Hermes denial, invalid adapter authority, duplicate
execution, audit redaction/retention/failures, and service readiness/lifecycle.

Final validation ran sequentially:

```text
python -m unittest discover -s tests  -> 74 passed
ruff check src tests                  -> passed
ty check                              -> passed
python -m pip check                   -> passed
secret-pattern scan                   -> no findings
git diff --check                      -> passed
Hermes pin/submodule check            -> clean at required revision
```

The full Hermes suite was intentionally not run. Static inspection plus focused
adapter tests were sufficient because upstream was not modified.

## Known limitations and deferred work

- Hermes compatibility is tied to the exact pin and requires re-audit on an
  upgrade.
- The real bridge may require locally configured Hermes dependencies and
  provider credentials even though deterministic tests do not.
- Credentials remain in a private file rather than Keychain.
- Approval and prepared records are process-memory-only; restart invalidates
  them.
- The server handles requests serially and has no supervisor/LaunchAgent.
- Hermes tools execute with the runtime user's OS privileges; strong process
  containment is not implemented.
- No native consent UI, Activity UI, SwiftUI, voice, wake word, computer
  control, local model, cloud backend, or broad integration was added.

Phase 4 should start only from a new explicit brief. The recommended first
slice is a native macOS lifecycle and consent client around the existing IPC,
keeping trusted approval issuance outside normal untrusted request payloads.
