# Phase 4C report

Status: paused before authorized host installation/TCC; live GUI validation has
not run.

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

The repository-only preflight did download verified assets into an exact
temporary directory and removed them after inspection. The sentence above
refers to persistent host provisioning: no driver package or app remains
installed.

## Stable host identity and signing

The JL app keeps the durable bundle ID `com.jlagent.control`. The build script
now accepts `JL_CODE_SIGN_IDENTITY` and uses that exact existing identity when
provided. This host has no valid Apple Development or Developer ID identity,
so the verified local build remains ad-hoc signed. Phase 4C does not fabricate
a certificate, modify Keychain trust, or make distribution claims.

This limitation does not destabilize Cua TCC because JL Agent is not the TCC
owner. The reviewed Cua release is signed by team `YCK386LBJ7` under
`com.trycua.driver`; its designated requirement is the stable identity that
receives Accessibility and Screen Recording.

The native consent private key remains a permanent Keychain RSA key with the
application tag `com.jlagent.native-consent.v1`. Signing now refuses to create
a replacement key when an enrollment file already exists but its Keychain key
is missing. Only the explicit Rotate Consent Key action replaces it.

The runtime hashes the public enrollment file at startup and exposes the
trusted SHA-256 fingerprint over authenticated status. It also re-hashes the
current private owned file on every status read. File loss or rotation while
the runtime is running makes `consent_enrollment_current` false and the live
computer-use readiness gate denies until the runtime restarts with the new
explicit enrollment. The private key never leaves Keychain.

## TCC onboarding and authoritative readiness

The native Permissions surface remains display-only and user-driven. It now
shows the foreground runtime PID, binary reachability, manifest version,
matching `CuaDriver.app` bundle/team identity, exact blocked reason, per-TCC
state, relaunch guidance, Settings links, and Recheck. It never invokes the
driver, requests a grant, or accepts a client permission assertion.

`ComputerUseExecutionReadiness` is the single runtime-owned live readiness
decision. It requires all of:

- the exact Hermes pin loaded successfully;
- authenticated local runtime status and active JL policy;
- current trusted native consent enrollment;
- computer-use enabled on supported macOS;
- a resolvable and manifest-reachable cua-driver;
- the complete Hermes 0.20+ runtime contract;
- a matching signed `CuaDriver.app` with an allowlisted official team;
- Accessibility granted to that driver identity;
- Screen Recording granted and currently capturable.

The standard MCP session starts on demand, so a separate persistent driver
service is not required. Manifest reachability is required. Unknown, denied,
missing, incompatible, unsigned, wrong-team, stale-enrollment, and
restart-required states all remain not ready.

## Foreground runtime lifecycle

Phase 4C keeps the foreground user-session process and does not add a
LaunchAgent or privileged helper. `scripts/run-local-runtime.sh` gives the
native development workflow one stable repository entry point and refuses to
guess when the small project `.venv` is absent. `--status` inspects only the
private readiness marker, PID liveness, and owned `0600` AF_UNIX socket.

Existing IPC ownership remains authoritative: an active endpoint rejects a
second runtime; a dead owned Unix socket is recoverable; wrong-type/unowned
paths are never removed. Startup errors are now concise on stderr, signals use
the existing graceful shutdown, and owned sockets/readiness are removed by
identity on shutdown. The Swift app does not spawn Python because that would
embed brittle checkout and environment assumptions; it shows running/unreachable
state and the authenticated runtime PID instead.

## Focused validation so far

Sequential checks completed before host installation:

```text
Provisioning asset SHA/signature audit             passed
Focused backend readiness/lifecycle/consent tests  20 passed
All JL-owned backend tests                         103 passed
Ruff                                                 passed
ty                                                   passed
pip check                                            passed
Swift format lint                                    passed
Native deterministic contract tests                 10 passed
Native release build and ad-hoc signature            passed
```

The focused adversarial coverage includes absent/unreachable/incompatible
driver state, missing/wrong driver app identity, missing TCC, consent enrollment
drift, stale runtime state, duplicate endpoint ownership, client health
override rejection, execution while health is not ready, post-consent action
mutation, foreground drift, replay, and native/direct-driver surface exclusion.
The full Hermes suite was not run.

## Live GUI smoke status

Not performed. The authoritative readiness gate is false because no installed
Cua Driver host or TCC grants exist. No screenshot, pointer movement, click,
typing, System Settings mutation, or direct-driver proof was attempted.
