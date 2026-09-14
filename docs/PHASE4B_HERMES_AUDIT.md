# Phase 4B pinned Hermes computer-use audit

Status: completed before JL implementation.

## Audited identity

- Repository: `upstream/hermes-agent`
- Revision: `044a77b3b6af4ce16138d42762f812a20b9f7a89`
- Version: `0.21.2`
- Upstream remains an unmodified Git submodule.

## Tool and backend path

Hermes exposes one registry tool named `computer_use` in the
`computer_use` toolset. `tools/computer_use_tool.py` is the discovery shim;
`tools/computer_use/schema.py` defines the single action-discriminated schema;
and `tools/computer_use/tool.py:handle_computer_use` applies Hermes hard blocks,
approval scopes, per-session locking, and dispatch.

The only production backend selected by default is
`tools.computer_use.cua_backend.CuaDriverBackend`. It resolves `cua-driver`
through `tools/computer_use/cua_backend_driver.py`, validates the driver's
0.20+ manifest contract, and speaks the driver's MCP protocol through a
per-Hermes-session backend. JL must invoke the existing Hermes `computer_use`
tool through its Phase 3B adapter; it must not call driver input APIs directly.

Supported model-facing actions are `capture`, `click`, `double_click`,
`right_click`, `middle_click`, `drag`, `scroll`, `type`, `key`, `set_value`,
`wait`, `list_apps`, `list_windows`, and `focus_app`.

## Screenshot and input paths

- `tools/computer_use/cua_backend_capture.py:_CaptureMixin.capture` resolves a
  window, records its PID/window ID, then calls the driver's `screenshot` or
  `get_window_state`; `get_desktop_state` is used only for the explicit
  full-screen lane.
- `tools/computer_use/tool.py:_capture_response` shapes the result and
  `_persist_capture_image` writes a bounded capture into Hermes' cache only when
  a returned image is present.
- `tools/computer_use/cua_backend_input.py:_InputMixin` sends pointer, keyboard,
  scrolling, drag, and value-setting calls to the sticky PID/window target from
  the preceding capture or focus selection.
- Background delivery is the default. Foreground delivery and persistent
  `bring_to_front` are explicit, separately approved choices. The native JL app
  must not perform these operations itself.

## Target and stale-state protections already in Hermes

- Input requires a sticky target; failed capture/transport reset clears it.
- An `app` value that provably differs from the sticky app is rejected before
  input dispatch.
- Snapshot `element_token` values are forwarded when the live driver advertises
  support, allowing the driver to reject stale element references.
- `capture_after` reuses exact PID/window identity where available.
- Transport success is not treated as proof of effect; Hermes returns a verdict
  that requires fresh capture/verification before retry or escalation.

JL will preserve these checks and add an outer approval fingerprint plus a
fresh foreground-context check for mutating actions. It will not weaken a
Hermes denial.

## Hermes guardrails

`tools/computer_use/tool.py` hard-blocks destructive system key combinations
and dangerous typed shell patterns before approval. All user-visible input
actions are marked destructive in Hermes' tool-local approval layer. Approval
state is session-scoped and foreground delivery has a distinct scope. JL's
policy and execution gate remain the outer mandatory boundary; Hermes' checks
remain defense in depth.

## macOS permissions and native component

The audited supported status command is
`cua-driver permissions status --json`, surfaced by
`tools/computer_use/permissions.py:computer_use_status`. On macOS, Hermes marks
computer use ready only when both of these driver-owned TCC grants are true:

- Accessibility: accessibility-tree access and mouse/keyboard input.
- Screen Recording: window and screen pixels.

The audited path does not report or require a separate Input Monitoring grant.
JL therefore must not request or display Input Monitoring in Phase 4B.

The grants attach to the `com.trycua.driver` CuaDriver identity. The standard
backend can run through a resolved `cua-driver` MCP command. `CuaDriver.app` is
specifically required when Hermes starts a private `bounded` or `unrestricted`
daemon on macOS, because LaunchServices preserves the TCC/signing identity.
`tools/computer_use/cua_backend_daemon.py` verifies the exact bundle identifier
and an allowlisted official signing team before that launch. Phase 4B uses the
standard mode and must not enable Hermes approval bypass.

The driver's normalized permission payload provides booleans, not a reliable
public distinction between macOS `denied` and `notDetermined`. JL may represent
both states, but an unclassified false probe must remain fail-closed as
`unknown` unless a more specific driver result is available.

## Side-effect-free validation boundary

Safe deterministic checks are source identity/evidence inspection, executable
resolution, manifest/status parsing through injected fakes, permission-state
mapping, and driver-absence handling. Health checks must not create a backend
session, capture pixels, move the pointer, type, focus an app, request TCC, run
an installer, or start a private daemon.

At this audit, the host has neither a resolvable `cua-driver` executable nor a
`CuaDriver.app` in `/Applications` or the user's Applications directory. No
installation or TCC prompt was attempted. Automated Phase 4B proof must use a
deterministic fake; real macOS computer-use validation remains a documented
host gap unless the operator installs and grants the audited component later.
