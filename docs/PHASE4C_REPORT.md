# Phase 4C report

Status: complete. Official Cua host provisioning, user-granted TCC, the
authoritative readiness gate, and one exact policy-gated live AX capture all
passed. Phase 5 has not started.

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

## Authorized host provisioning outcome

After explicit authorization, the reviewed installer placed the official
`CuaDriver.app` at `/Applications/CuaDriver.app` and its CLI at
`~/.local/bin/cua-driver`. The installed v0.28.0 bundle is identified as
`com.trycua.driver`, signed by team `YCK386LBJ7`, uses Hardened Runtime, and
passes signature/notarization and pinned-Hermes manifest compatibility checks.

The user manually granted Accessibility and Screen Recording to CuaDriver.
JL Agent, Terminal, Codex, and Python were not granted computer-use TCC. No
System Settings automation, `tccutil`, unsigned-driver override, unrestricted
mode, or TCC bypass was used.

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

## Live-path corrections

The first authorized AX capture exposed a production adapter defect: an exact
gate-authorized tool call constructed the full Hermes `AIAgent`, so the inert
`native-validation` route was treated as an LLM provider before tool dispatch.
Commit `ec7f646` replaced that construction with pinned Hermes'
`model_tools.handle_function_call`, preserving request middleware,
pre-dispatch guards, execution middleware, registry dispatch, tool/toolset
constraints, native `computer_use`, and Hermes denial behavior. No generic raw
tool endpoint or provider was added.

A later manual attempt correctly hit the 30-second consent replay defense after
its challenge expired. Commit `554d77d` makes expired consent return
`consent_expired`, terminates its linked request denied without issuing an
approval, and prevents the native client from signing or submitting an expired
challenge. Genuine approved/rejected reuse remains `consent_replayed`.

The successful backend smoke then revealed only a client presentation defect:
the native AF_UNIX client stopped waiting after two seconds while the first
lazy Hermes/Cua startup legitimately continued. Commit `b07e3bb` retains the
two-second timeout for status, activity, prepare, and consent, while giving
only `execute` a bounded 90-second response deadline. The backend request had
already completed successfully once, so the user explicitly prohibited a
post-fix live retry.

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

## Final validation

Sequential checks completed after the live-path corrections:

```text
Provisioning and installed signature/manifest      passed
All JL-owned backend tests                         106 passed
Ruff                                                 passed
ty                                                   passed
pip check                                            passed
Swift format lint                                    passed
Native deterministic contract tests                 13 passed
Native release build and ad-hoc signature            passed
Secret-pattern and Git diff checks                    passed
Hermes pin and submodule cleanliness                  passed
```

The focused adversarial coverage includes absent/unreachable/incompatible
driver state, missing/wrong driver app identity, missing TCC, consent enrollment
drift, stale runtime state, duplicate endpoint ownership, client health
override rejection, execution while health is not ready, post-consent action
mutation, foreground drift, replay, and native/direct-driver surface exclusion.
The full Hermes suite was not run.

## Live GUI smoke result

PASS at the authoritative backend boundary. The user manually sent and
approved exactly one final safe request:

```text
Capability: core.hermes.computer-use
Action: computer_use
Permissions: screen.capture
Target: com.jlagent.control
Foreground: com.jlagent.control
Arguments: {"action":"capture","mode":"ax","app":"com.jlagent.control"}
Target is within workspace: OFF
Reversible: ON
```

Audit request `5ce6215c-2aab-4d80-9807-0b23a7d370b9` records
`execution_prepared -> execution_started -> execution_completed`, with the
exact approval consumed once and a duration of 18,619 ms. The call traversed
authenticated IPC, JL policy and signed native consent, final execution-gate
revalidation, the corrected Hermes dispatcher, pinned native `computer_use`,
and CuaDriver. CuaDriver's MCP child started during the cold path and the AX
capture completed without an upstream error.

The UI reported a timeout because its old two-second response deadline elapsed
before the successful server response. That false client failure is corrected
and covered deterministically by the 13-test native suite. No second backend
execution was attempted after this successful request, no pointer or keyboard
action was used, and Phase 5 was not started.
