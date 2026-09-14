# Phase 4B report

## Outcome

Phase 4B adds the smallest safe macOS permission and computer-control slice.
Hermes Agent remains the sole computer-use engine, unchanged at
`044a77b3b6af4ce16138d42762f812a20b9f7a89` (`0.21.2`). JL projects the
existing `computer_use` tool, derives readiness from the audited cua-driver
contract, applies its existing permission/risk engine, binds target context,
and dispatches only through the Phase 3B execution gate and thin Hermes adapter.

No voice, autonomous GUI planner, second automation engine, installer,
LaunchAgent, or TCC bypass was added.

## Pinned Hermes audit

The complete source audit is in `PHASE4B_HERMES_AUDIT.md`. The relevant
upstream path is:

```text
tools/computer_use_tool.py registry shim
  -> tools/computer_use/schema.py single computer_use schema
  -> tools/computer_use/tool.py policy/approval/dispatch
  -> tools/computer_use/cua_backend.py CuaDriverBackend
  -> cua_backend_capture.py / cua_backend_input.py
  -> cua-driver MCP -> macOS
```

Hermes already owns screenshots, AX state, mouse/keyboard input, sticky
PID/window targeting, snapshot element tokens, capture-after verification,
background-first delivery, foreground escalation, unsafe key/text hard blocks,
and driver-side guardrails. JL did not copy any of these implementations.

## macOS permissions and native component

The audited macOS path requires exactly:

- Accessibility for accessibility elements and mouse/keyboard delivery.
- Screen Recording for window or screen pixels.

The pin does not require or expose a separate Input Monitoring grant, so JL does
not request or display it. TCC attaches to CuaDriver's
`com.trycua.driver` identity, not JL's SwiftUI process.

The standard backend requires a resolvable cua-driver binary with the Hermes
0.20+ manifest contract. `CuaDriver.app` is required only when Hermes launches a
private `bounded` or `unrestricted` daemon on macOS; Phase 4B uses standard mode
and never enables approval bypass.

`HermesComputerUseReadinessProbe` performs only bounded `manifest` and
`permissions status --json` reads. It never starts MCP, captures, moves input,
prompts for TCC, installs, or starts a daemon. JL represents `granted`, `denied`,
`notDetermined`, `unknown`, `unavailable`, and `restartRequired`. The pinned
driver's false boolean cannot reliably distinguish denied from not determined,
so JL exposes it as `unknown` and fails closed. A granted but non-capturable
Screen Recording state becomes `restartRequired`.

## Native Permissions UI

The Phase 4A SwiftUI shell now has a compact **macOS Permissions** section. It
shows computer-use health, driver state/version, Accessibility and Screen
Recording state, why each permission is required, and explicit System Settings
links. It explains that grants belong to CuaDriver and when a relaunch may be
needed. It does not trigger TCC automatically, repeat prompts, change System
Settings, or expose any direct automation API.

## Capability and health

`core.hermes.computer-use` is a read-only JL descriptor projection of the
Hermes `computer_use` toolset/tool. Its descriptor includes `local.read`,
`screen.capture`, `input.control`, and the existing stronger scopes for
destructive, external communication, credential, and financial effects.

Health is derived as follows:

| Condition | Health |
|---|---|
| Operator disabled | `disabled` |
| Unsupported platform or missing binary | `unavailable` |
| Invalid/old driver manifest | `misconfigured` |
| Missing, unknown, denied, or restart-pending required TCC | `unavailable` |
| Source, driver contract, and both grants ready | `healthy` |

The computer-use health observation is runtime-owned. A client-supplied probe
cannot override it.

## Risk and permission rules

The existing `PermissionRiskEngine` remains authoritative. It validates the
inner `computer_use.arguments.action` and requires a non-forgeable minimum
scope:

- `capture` requires `screen.capture`.
- `list_apps`, `list_windows`, and `wait` require `local.read`.
- click variants, drag, scroll, type, key, set-value, and focus require
  `input.control`.

Protected screen reads and every input-control action require native consent.
Mutating UI actions cannot run unattended. Missing base scope, unsupported
action, missing consent surface, or imprecise target context is denied. If an
operation also sends externally, uses credentials, destroys state, or has
financial/high-risk effect, its existing stronger class remains in the same
decision and approval fingerprint. JL never weakens a Hermes/upstream denial.

## Foreground and target integrity

Every mutating computer-use request must carry an exact `app` target, the same
resolved target, and an observed foreground application identity. These fields,
the full normalized arguments, action, caller/session, descriptor identity, and
permissions are covered by the existing SHA-256 approval fingerprint.

The native client observes the foreground application before preparation and
again immediately before execution. The execution gate also invokes
`ComputerUseTargetGuard`; the production macOS probe reads frontmost identity
with `lsappinfo`, without Accessibility or Apple Events. Missing identity or
focus drift returns `target_context_changed` before Hermes. Hermes then retains
its stricter sticky PID/window, app-mismatch, element-token, and post-action
verification checks.

## Narrow end-to-end proof

The safe proof is `computer_use(action="capture", mode="ax")`. It reads a
deterministic accessibility-tree-shaped result and performs no input. Hermes'
noop backend returns no pixel data during this proof; the real audited capture
path still requires Screen Recording. The cross-process smoke passed through:

```text
Swift native client
  -> Keychain credential and protocol-v1 AF_UNIX
  -> JL trusted health and screen-capture policy
  -> Keychain RSA signed exact consent
  -> one-time preparation and execution gate
  -> HermesExecutionAdapter computer_use toolset/tool
  -> pinned Hermes handle_computer_use
  -> Hermes' noop deterministic backend
```

The noop backend is Hermes' own test backend. It proves the pinned handler and
JL integration without pretending the host has a real driver or permission.
The smoke cleans its exact `/private/tmp` directory and test Keychain items.

## Adversarial coverage

Focused tests cover missing Accessibility, missing Screen Recording, absent
driver, invalid driver contract, disabled capability, untrusted health input,
missing/changed target, stale foreground app, changed action/arguments after
consent, unapproved mutating input, unattended input, repeat execution,
approval/consent replay, Hermes-side denial, forged adapter authority, and
native source exclusion of Hermes/CGEvent/AXUIElement/screenshot APIs.

## Validation

The sequential Phase 4B validation is deterministic and makes no provider,
model, network, or paid request:

```text
JL-owned unittest suite                         99 passed
Ruff                                            passed
ty                                              passed
pip check                                       passed
Swift focused native contract tests             9 passed
Swift format lint                               passed
SwiftUI release app build/ad-hoc sign           passed
Swift-to-Python-to-pinned-Hermes noop proof     passed
```

The full Hermes suite was intentionally not run. The submodule source/pin and
cleanliness were checked directly.

## Real-host validation and limitations

This host has no resolvable cua-driver binary and no CuaDriver.app in the
standard Applications locations. No installer, TCC prompt, System Settings
change, screenshot, pointer, or keyboard action was attempted. Real macOS
computer use is therefore explicitly **not validated**; deterministic coverage
must not be reported as a live UI/TCC pass.

The foreground probe can fail when the runtime has no GUI session; mutating
input then fails closed. Phase 4B supports one exact action per request and does
not add multi-step GUI planning. Operators must truthfully include stronger
effect scopes for communication, credential, destructive, or financial actions;
those effects remain denied or confirmation-bound by existing policy. Production
distribution signing, lifecycle/onboarding, and stable consent enrollment remain
deferred.
