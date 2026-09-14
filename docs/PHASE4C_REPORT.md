# Phase 4C report

Status: in progress; host provisioning and live GUI validation have not run.

## Scope boundary

Phase 4C makes the existing native/runtime/computer-use stack deployable and
verifiable on this Mac without adding another automation engine, a privileged
helper, a LaunchAgent, voice, broad desktop workflows, or a TCC bypass. Hermes
remains the sole computer-use engine at
`044a77b3b6af4ce16138d42762f812a20b9f7a89` (`0.21.2`).

## Recovered source and host state

Revalidated on 2026-09-14 ICT before implementation:

- Branch `main` was clean and `HEAD == origin/main == 05d742a` after a fresh
  fetch.
- The Hermes submodule was clean at the required revision.
- The host is Apple Silicon (`arm64`) running macOS 26.5.1.
- No `cua-driver` resolved and no `CuaDriver.app` existed in the standard
  Applications locations.
- No user-local JL runtime directory existed, so no runtime, enrolled public
  consent key, readiness file, or socket was active.
- The previous release build existed at
  `macos-app/.build/arm64-apple-macosx/release/JL Agent.app` and passed
  `codesign --verify --deep --strict`.
- That build used ad-hoc signing, bundle ID `com.jlagent.control`, no team ID,
  and a cdhash-based designated requirement. `security find-identity` reported
  no valid code-signing identities.

## Cua host component contract audit

### Components and relationship

At the pinned Hermes revision, the production chain is:

```text
tools/computer_use_tool.py registry shim
  -> tools/computer_use/tool.py policy and dispatch
  -> CuaDriverBackend
  -> _CuaDriverSession
  -> manifest-selected `cua-driver ... mcp` stdio process
  -> CuaDriver.app-hosted macOS driver identity
  -> macOS accessibility, capture, and input facilities
```

`cua-driver` is the CLI executable Hermes resolves and launches. It advertises
the MCP invocation and supported daemon verbs through `cua-driver manifest`.
Hermes communicates over MCP stdio for the standard session. A session process
is long lived only for that Hermes backend and is stopped by Hermes teardown.

`CuaDriver.app` is not a second computer-use engine. It is the macOS app bundle
that carries the same driver executable under the stable
`com.trycua.driver` identity. The official macOS release installer places the
bundle at `/Applications/CuaDriver.app` and links
`~/.local/bin/cua-driver` to its executable. Current official driver source
documents that `cua-driver mcp` uses the app-hosted identity so TCC attribution
survives terminal, Python, and JL app rebuilds.

Both artifacts are therefore required for this Mac deployment:

- Hermes needs a resolvable executable for manifest, permission, doctor, and
  MCP calls.
- macOS needs the matching app bundle for the stable signed TCC host identity.

For Hermes private `bounded` or `unrestricted` sessions,
`tools/computer_use/cua_backend_daemon.py` additionally refuses to launch
unless the resolved executable is inside the matching app bundle. JL keeps the
standard mode and never enables `unrestricted` or approval bypass.

### Compatibility and exact checks

The pinned contract in
`tools/computer_use/cua_backend_driver.py` requires:

- semantic `binary_version >= 0.20.0`;
- a valid `mcp_invocation.args` list;
- `mcp --socket` and `mcp --grant`;
- `serve --socket`, `--permission-mode`, `--capability-manifest`,
  `--approve-capability-manifest`, and `--embedded`;
- `stop --socket`.

There is no upper version pin in Hermes 0.21.2. Compatibility is established
at runtime from the manifest, then from the permission and doctor probes; an
unknown or incomplete response is not ready.

The latest published stable Cua Driver release confirmed during this audit is
`cua-driver-rs-v0.28.0`, source tag commit
`1b50c02e2d34734f64d2d22f54eb76cc97b4a663`. The release publishes SHA-256
checksums for the installer, helper, and macOS archives. A newer tag without a
published, checksummed release is not selected merely because it exists.

### Signing and TCC ownership

Hermes records the exact bundle identifier `com.trycua.driver` and allowlists
official release team IDs `4YEC26S9KF` and `YCK386LBJ7` for its private-daemon
launch path. Ad-hoc driver builds are rejected unless an explicit
development-only Hermes override is enabled; JL will not enable that override.

The TCC owner is the signed executable hosted by `CuaDriver.app`:

| Permission | Owning identity | Consumer |
|---|---|---|
| Accessibility | `com.trycua.driver` | AX tree plus pointer/keyboard delivery |
| Screen Recording | `com.trycua.driver` | window/screen pixel capture |

The JL SwiftUI app does not receive either permission. The JL Python runtime
does not receive either permission. The native app remains a status/request/
consent client, and the Python runtime remains the policy and execution
authority.

Input Monitoring is not reported or required by the pinned Hermes path and is
not requested.

### Startup and provisioning source

Pinned Hermes exposes these supported commands:

```text
hermes computer-use install
hermes computer-use status
hermes computer-use doctor
hermes computer-use permissions status --json
hermes computer-use permissions grant
```

Its installer entry point currently fetches the trycua installer from `main`.
That default is not reproducible enough for Phase 4C and is not run directly.
The safe JL workflow must instead select a published version, use the official
tag/release assets, verify their published SHA-256 values before execution,
pin `CUA_DRIVER_RS_VERSION`, avoid shell-profile edits, and inspect the
installed bundle's code signature, identifier, team, and manifest afterward.

The official Cua source/build path exists under
`libs/cua-driver/rust` with `libs/cua-driver/scripts/install-local.sh`, but it
produces a separate local identity (`com.trycua.driver.local`) unless stable
signing is available. It is useful for Cua development, not a substitute for
the official signed release required for stable JL TCC enrollment.

## Current stop boundary

No driver package was downloaded or installed, no app was registered with
LaunchServices, no driver process was started, and no TCC prompt was triggered.
Host provisioning writes outside this repository and will be performed only
after the repository-side workflow is reviewed and the user explicitly runs or
authorizes it. Accessibility and Screen Recording will remain user-granted in
System Settings; Phase 4C will stop before those grants if they become the next
required action.
