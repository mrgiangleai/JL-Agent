# Phase 9 Proposal — Personal Daily-Driver v1

**Status:** Step 5 checkpoint complete; Step 6 functionality and Step 7 remain
**Date:** 2026-09-17 ICT
**Target:** This Mac and this user account only

## Decision

Phase 9 is reduced to making the existing JL Agent usable every day on this
Mac. It is not a general distribution project.

The remaining personal-v1 conditions are bounded:

1. The current SwiftPM build is reproducible with the installed CommandLineTools
   compiler when it is paired with the installed macOS 15.5 SDK and a writable
   project-local module cache.
2. Actual voice functionality requires the existing JL voice boundary, a
   working voice host, and a normal user-granted Microphone permission. Stable
   microphone permission persistence across rebuilds is not required for this
   personal v1; macOS may ask the user to grant it again after a rebuild or
   update.

Paid Apple Developer enrollment, stable Apple-issued signing, Developer ID,
notarization, and distribution must not block personal v1 unless validation
proves one is technically required for actual voice or CuaDriver functionality.
JL must never bypass or weaken macOS TCC.

No signing identity, certificate, provisioning profile, or TCC state was
changed. Step 1 implementation is limited to the packaging and lifecycle
changes described below.

## Step 1 final status — personal packaging and lifecycle

Implemented only the personal daily-driver packaging/lifecycle slice:

- `JL Agent.app` now starts or reconnects to one packaged foreground runtime
  from the app task; no Terminal or `.venv` activation is needed.
- JL source, configuration, the pinned Hermes subset, Phase 6 patched files,
  and a build-time Hermes revision marker are bundled under
  `Contents/Resources/JLRuntime`.
- The launcher uses the known Python 3.13 installation on this Mac and no
  longer resolves JL or Hermes code from the developer checkout.
- App-owned start, bounded readiness, safe stop/restart, duplicate-owner
  refusal, and reconnect-on-refresh are present. Stop never terminates a
  runtime owned by another process.
- Runtime state remains under the existing macOS Library runtime directory;
  model cache is under `~/Library/Caches/JL Agent`, and runtime diagnostics are
  under `~/Library/Logs/JL Agent`.
- Runtime startup checks internal APFS before creating runtime state. Existing
  approval, voice, and automation admission state is not replayed by this
  lifecycle path; stable signing/TCC persistence remains deferred while actual
  voice/Cua functionality remains in Step 6 scope.

Focused evidence: Swift release build passed with the documented lane; Hermes
projection tests passed (4), runtime-service tests passed (7) in the normal
macOS context; the packaged runtime reached ready, stopped cleanly, and a
second invocation was rejected as an active duplicate. No live voice, Cua, or
inference was run.

### Deferred signing requirement

This Mac has no valid Apple signing identity and the current app is ad-hoc
signed. Stable Apple-issued signing and persistent Microphone TCC are deferred
for personal v1. Voice functionality still requires the actual
`com.jlagent.voice-runtime` host and an explicit user-granted Microphone
permission; a rebuild may require another grant. JL must not own or bypass
Microphone TCC, and CuaDriver must retain its own official TCC owner.

## Step 2 review status — supported build/sign configuration

Implemented only the build/sign configuration slice from the implementation
sequence:

- SwiftPM build scripts share the supported Command Line Tools lane:
  `MacOSX15.5.sdk`, `CLANG_MODULE_CACHE_PATH`, and
  `SWIFTPM_MODULECACHE_OVERRIDE` under the project-local `.build` directory.
- `JL Agent` may remain ad-hoc signed for development. A configured app
  identity must be a valid Apple Development identity.
- Stable Apple Development signing remains an optional stable-identity path,
  not a personal-v1 product gate. No certificate enrollment, provisioning,
  signing identity, or TCC state was changed.
- Native contract compilation has a matching wrapper so it uses the same
  supported Swift lane.

The bundle identifiers remain `com.jlagent.control` and
`com.jlagent.voice-runtime`. Packaging and runtime lifecycle are already
satisfied by Step 1 and remain outside this Step 2 slice. Library migration,
voice-host implementation, CuaDriver validation, Settings/Diagnostics, and
lazy optional initialization remain outside this Step 2 slice.

## Step 5 review status — private Library state and forward migration

Implemented only the Library layout and forward-migration slice:

- Authoritative runtime state is under
  `~/Library/Application Support/JL Agent/runtime/`; Hermes-owned state is under
  `~/Library/Application Support/JL Agent/hermes/`.
- Rebuildable model assets use `~/Library/Caches/JL Agent/models/`, and runtime
  diagnostics use `~/Library/Logs/JL Agent/`. The native process controller uses
  the same documented log location.
- Known legacy Hermes/automation state and the project-local `.jl-agent/models`
  cache are copied through private atomic staging directories. Existing source
  directories are preserved, and conflicts, symlinks, unsupported files, or
  unsafe destinations fail closed before runtime startup.
- The runtime sets `HERMES_HOME` to the Library Hermes profile and rejects a
  conflicting inherited value. No second Hermes core, memory store, capability,
  integration, or trust-boundary change was introduced.

Focused evidence: runtime-service tests passed (9), including migration,
source preservation, and conflict failure; Python compilation and diff checks
passed; the Swift release app build passed with the documented build lane.

## 1. Build blocker — exact supported setup on this Mac

### Observed host

- macOS `26.5.1`, build `25F80`, Apple Silicon.
- `xcode-select --print-path` is
  `/Library/Developer/CommandLineTools`.
- No `/Applications/Xcode.app` is installed; `xcodebuild` is unavailable.
- Active Swift compiler:
  `Apple Swift version 6.3.2 (swiftlang-6.3.2.1.108 clang-2100.1.1.101)`.
- Default SDK is `MacOSX26.5.sdk`.
- Available SDKs include `MacOSX15.5.sdk`.

The default command fails before compiling the package because the default SDK
Swift interface was built with compiler `6.3.2.1.2`, while the active compiler
is `6.3.2.1.108`. The default command also attempts to write the global Clang
module cache under a location that is not writable in this environment.

### Passing build lane

This exact command completed successfully without changing production source or
installing dependencies:

```bash
cd macos-app
SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX15.5.sdk \
CLANG_MODULE_CACHE_PATH="$PWD/.build/ModuleCache" \
SWIFTPM_MODULECACHE_OVERRIDE="$PWD/.build/ModuleCache" \
swift build -c release --product JLAgentApp
```

Result: `Build of product 'JLAgentApp' complete!`.

### Required build conclusion

For the current SwiftPM project, the supported reproducible lane is:

- current CommandLineTools Swift compiler `6.3.2.1.108`;
- `MacOSX15.5.sdk`, selected explicitly with `SDKROOT`;
- writable project-local `CLANG_MODULE_CACHE_PATH` and
  `SWIFTPM_MODULECACHE_OVERRIDE`;
- no reliance on the inaccessible global SwiftPM/Clang caches.

The default SDK must not be mixed with this compiler. A future Xcode-based
lane is acceptable only when Xcode's `swiftc` and SDK are from the same
toolchain; Xcode installation or selection is outside this discovery task.

## 2. Signing and permission persistence — deferred unless technically required

### Local inventory

- `security find-identity -v -p codesigning`: **0 valid identities found**.
- No local `Apple Development`, `Mac Development`, or `Developer ID Application`
  identity was found in the login keychain.
- Existing `JL Agent.app` is ad-hoc signed:
  `Identifier=com.jlagent.control`, `TeamIdentifier=not set`.
- Its designated requirement is only a build-specific `cdhash`, not a stable
  Apple-team identity.
- `build-voice-runtime.sh` correctly rejects ad-hoc signing.
- `JLVoiceRuntime` is currently an inert signed-host placeholder; it does not
  own the real microphone path yet.

### Options

| Option | Paid Apple membership | Suitable for personal daily-driver v1? | Decision |
|---|---:|---|---|
| Ad-hoc / `Sign to Run Locally` | No | Yes for personal v1 if actual functionality works; Microphone permission may need to be granted again after rebuilds. | Acceptable personal-v1 path. |
| Free Personal Team | No | Not required. Its provisioning lifetime is limited and managed by Xcode. | Defer unless technically needed. |
| Apple Development certificate | Yes | Optional. It provides stable identity/persistence but is not required by personal-v1 scope. | Defer unless technically needed. |
| Developer ID Application | Yes | Technically yes, but it is for outside-App-Store distribution and is unnecessary while distribution/notarization are deferred. | Defer. |
| Self-signed/custom local certificate | No | Not required; do not use it as a TCC bypass or substitute for normal user consent. | Defer. |

### Minimum personal-v1 permission strategy

- Actual Hermes voice capture must run under `com.jlagent.voice-runtime`, not
  the generic Python runtime or `com.jlagent.control`.
- The user must grant Microphone permission through normal macOS TCC. A
  rebuild or update may require the user to grant it again.
- The main JL app must not request or own Microphone TCC, and JL must not
  automate, mutate, bypass, or weaken TCC.
- CuaDriver remains separate and keeps its official identity
  `com.trycua.driver` / team `YCK386LBJ7` and its own Accessibility and Screen
  Recording TCC grants.
- If a stable Apple identity is later available, it may be used consistently,
  but stable Team ID/designated-requirement persistence is not a personal-v1
  precondition.

### Is paid membership required?

Not for a temporary local proof: Apple provides a free Personal Team for
testing, but its provisioning lifetime is limited and it is managed by Xcode.

Paid membership is **not** required by personal-v1 scope. It may be considered
only if a normal user-granted permission flow cannot provide the actual voice or
CuaDriver functionality on this Mac. Enrollment, purchase, installation, and
TCC changes remain outside this decision update.

## 3. Reduced Phase 9 v1 goals

### In scope

1. **Launch without Terminal**

   Launching `JL Agent.app` starts or connects to exactly one packaged,
   user-session JL runtime. Terminal, the repository path, and a developer
   shell are not required.

2. **Packaged runtime**

   Bundle the JL runtime source/configuration, pinned Hermes subset, build-time
   Hermes revision marker, Phase 6 patched files, and required manifests. On
   this personal Mac the launcher uses the already-installed Python 3.13
   runtime; it does not activate `.venv` or resolve code from the checkout.
   General self-contained dependency bundling remains outside this Step 1
   slice.

3. **Safe restart and crash recovery**

   Keep the existing AF_UNIX/readiness ownership and stale-endpoint recovery.
   Add one lifecycle owner with bounded reconnect and safe restart behavior.
   A restart may recover the process and transport only. It must not replay
   pending approvals, prepared actions, voice listening, or scheduler work.
   Phase 6 global stop remains effective after restart.

4. **User data in macOS Library locations**

   Move operational state out of the checkout:

   ```text
   ~/Library/Application Support/JL Agent/runtime/   authoritative runtime state
   ~/Library/Application Support/JL Agent/hermes/     Hermes-owned profile/state
   ~/Library/Caches/JL Agent/models/                  rebuildable model cache
   ~/Library/Logs/JL Agent/                          redacted diagnostics
   ```

   Preserve Keychain semantics, private file modes, bounded audit retention,
   and Hermes ownership of sessions, memory, skills, and scheduler state. Do
   not add a second memory or session store.

5. **Stable permission ownership**

   - Microphone belongs only to `com.jlagent.voice-runtime`; normal user-granted
     permission remains required and may need regrant after rebuilds.
   - Accessibility and Screen Recording belong only to the official signed
     CuaDriver identity.
   - JL reports readiness and gives user-driven System Settings links; it never
     bypasses, mutates, or repeatedly prompts for TCC.

6. **Minimal Settings/Diagnostics**

   Show runtime state/PID/version, Hermes pin, voice-host identity and
   activation state, CuaDriver identity/version/TCC readiness, state/cache/log
   locations, migration status, and a redacted diagnostic export. Include
   explicit restart, credential refresh, consent-key rotation, and safe cache
   repair actions. Do not expose credentials, keys, raw audio, prompts,
   reasoning, or unrestricted arguments.

7. **Lazy startup**

   Ordinary launch performs only cheap status, credential/key availability,
   transport, and readiness checks. Defer Skill Manager listing, voice
   requirement/model probes, automation history, model construction,
   microphone capture, wake listening, scheduler worker start, and provider or
   inference work until the user explicitly opens or starts those surfaces.

## 4. Minimal v1 lifecycle

```text
JL Agent.app
  -> one user-session lifecycle owner
  -> packaged JL runtime helper
  -> authenticated JL AF_UNIX + private consent sockets
  -> existing JL gates and thin adapters
  -> pinned Hermes core
       |-> signed JL voice host (Microphone)
       |-> official CuaDriver (Accessibility/Screen Recording)
```

The lifecycle owner is an operational mechanism only. It must not become a
second agent, scheduler, model loop, memory store, or tool executor.

Normal shutdown order:

1. close new admission;
2. stop optional workers and release voice/wake ownership;
3. drain only bounded in-flight work;
4. close consent and normal sockets;
5. remove only owned readiness/endpoints;
6. retain user state and audit data.

Crash/reboot behavior:

- restart the runtime process if the user has enabled runtime-at-login;
- reconnect the UI with bounded backoff;
- recover owned stale sockets/readiness markers;
- keep voice, scheduler execution, pending consent, and prepared actions
  stopped until explicitly reactivated;
- never auto-replay a side effect or old approval.

## 5. Explicitly deferred

- General distribution and clean-machine installation.
- Paid Apple Developer enrollment and stable Apple-issued signing, unless
  technically required for actual voice/CuaDriver functionality.
- Developer ID distribution signing.
- Notarization.
- Full rollback system; v1 needs only safe migration failure handling and
  preservation of the previous user-data directory.
- Auto-update.
- New integrations, providers, MCP servers, notifications, autonomous jobs,
  model-facing Skill tools, skill execution, memory work, or any new agent
  capability.

## 6. Smallest implementation sequence after approval

1. Freeze the personal-v1 bundle IDs, Team ID/signing requirements, Library
   layout, lifecycle states, and no-auto-resume rules.
2. Add the supported build/sign configuration around the already passing
   toolchain lane; do not modify production behavior to hide toolchain errors.
3. Package the existing runtime and Hermes resources so launch no longer uses
   `.venv` or checkout paths.
4. Add the single user-session lifecycle owner, app launch/connect/restart,
   stale recovery, bounded reconnect, and safe shutdown.
5. Move operational state/model caches/logs to Library locations and add only
   forward migration with preservation of the old directory on failure.
6. Complete the actual voice-host functionality and verify CuaDriver readiness
   paths. Keep normal user-granted TCC mandatory; defer stable signing,
   persistent TCC identity, and distribution unless technical validation proves
   they are required for the functionality itself.
7. Add minimal Settings/Diagnostics and lazy optional initialization.

## Definition of Done for personal v1

- `JL Agent.app` launches on this Mac without Terminal, `.venv`, or a checkout
  path.
- The packaged runtime starts exactly once, reconnects after restart, refuses
  duplicate ownership, and recovers owned stale transport state.
- Crash/reboot recovery never replays consent, prepared actions, voice, or
  scheduler work; Phase 6 stop semantics remain intact.
- Runtime, Hermes state, audit, logs, models, credentials, and caches have
  documented private Library locations and permissions.
- The Swift build passes with the documented compiler/SDK/cache setup.
- Voice functionality runs under `com.jlagent.voice-runtime` with normal
  user-granted Microphone TCC; permission may need to be granted again after a
  rebuild or update. Stable Apple signing is not required unless technically
  proven necessary for the functionality.
- CuaDriver TCC remains owned by `com.trycua.driver`; JL does not request,
  mutate, bypass, or weaken either permission boundary.
- Settings/Diagnostics explains blocked states and exports only redacted,
  bounded metadata.
- Startup does not initialize model inference, microphone, wake, scheduler, or
  optional Skill/automation surfaces before explicit user action.
- Hermes remains the sole core; all existing JL security gates, Phase 6 patch,
  Phase 7 assistant architecture, Phase 8 Skill Manager, and deferred memory
  boundary remain unchanged.

## Evidence and references

Repository evidence:

- `docs/PHASE8_HANDOFF.md`, `docs/PHASE8_REPORT.md`, `docs/PROGRESS.md`;
- `docs/ARCHITECTURE.md`, `docs/SECURITY_MODEL.md`;
- `docs/PHASE4C_HANDOFF.md`, `docs/PHASE5_HANDOFF.md`,
  `docs/PHASE6_HANDOFF.md`, `docs/PHASE7_HANDOFF.md`;
- `src/jl_agent/runtime_service.py`, `src/jl_agent/control/voice.py`,
  `macos-app/Scripts/build-app.sh`, and
  `macos-app/Scripts/build-voice-runtime.sh`.

Apple references:

- [TN3127: Inside Code Signing — Requirements](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements)
  — macOS uses the designated requirement to track microphone identity; ad-hoc
  identity is tied to a specific code version.
- [Apple Developer account overview](https://developer.apple.com/help/account/basics/about-your-developer-account)
  — free Personal Team testing is limited and requires periodic reprovisioning;
  program membership unlocks Certificates, Identifiers & Profiles.
- [Certificates overview](https://developer.apple.com/help/account/create-certificates/certificates-overview)
  — Apple Development/Mac Development and Developer ID certificate roles.
- [Developer ID certificates](https://developer.apple.com/help/account/certificates/create-developer-id-certificates)
  — Developer ID is for outside-App-Store distribution and is deferred here.

**Phase 9 Step 1 stops here. No certificate enrollment, dependency
installation, TCC change, live inference, live voice, live CUA, or heavy test
run was performed.**
