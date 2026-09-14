# Phase 4C paused host handoff

## Read this first

Phase 4C repository work is implemented and validated, but the phase is not
complete. Work is intentionally paused before host installation and macOS TCC.
Do not claim a live GUI pass and do not bypass or manipulate TCC.

Read in this order:

1. `docs/PHASE4C_REPORT.md`
2. `docs/PHASE4C_HOST_SETUP.md`
3. `docs/PHASE4B_HERMES_AUDIT.md`
4. `docs/ARCHITECTURE.md`
5. `docs/SECURITY_MODEL.md`
6. `docs/PROGRESS.md`

## Repository checkpoint

- Required Hermes pin:
  `044a77b3b6af4ce16138d42762f812a20b9f7a89` (`0.21.2`).
- Phase 4C commits so far:
  - `a10e7d0` — source/host contract audit.
  - `f222698` — reviewed, checksum-pinned provisioning workflow.
  - `5abb16b` — host identity/readiness, onboarding, runtime lifecycle, and
    consent enrollment stability.
- Revalidate Git/remote/submodule state on resume; do not trust recorded SHAs
  as current without checking.

## Current host state

- `cua-driver`: not installed or resolvable.
- `CuaDriver.app`: absent from standard locations.
- Authoritative driver/TCC readiness: unavailable/not ready.
- Foreground JL runtime: stopped; no readiness marker exists.
- JL release app:
  `macos-app/.build/arm64-apple-macosx/release/JL Agent.app`.
- JL app signing: ad-hoc, `com.jlagent.control`, no team ID. No Apple code-sign
  identity is installed on this Mac.
- Live GUI smoke: not performed.

## Exact resume boundary

The reviewed preflight already passed without installing anything. The next
operation writes `/Applications/CuaDriver.app`, creates the user-local CLI
link, and registers the app with LaunchServices, so it requires explicit user
authorization:

```bash
./scripts/provision-cua-driver.sh --install
```

After installation, inspect only:

```bash
/Applications/CuaDriver.app/Contents/MacOS/cua-driver permissions status --json
```

If either grant is false/unknown, stop automation. The user must enable
**CuaDriver** manually in System Settings -> Privacy & Security for:

1. Accessibility
2. Screen & System Audio Recording / Screen Recording

Do not grant these to JL Agent, Terminal, or Python. Do not add Input
Monitoring. Do not run `tccutil`, alter SIP/Gatekeeper, remove quarantine as a
normal solution, or enable Hermes unsigned/unrestricted bypasses.

## After the user returns

1. Re-run driver manifest/signature/permission readiness probes.
2. Launch the native app once to establish or reuse the Keychain consent key
   and public enrollment file.
3. Start the foreground runtime with `./scripts/run-local-runtime.sh`, then use
   Refresh Credential/Recheck in the native app.
4. Confirm `execution_ready == true` from authenticated runtime status.
5. Only then perform one harmless JL-owned GUI test through native client ->
   authenticated IPC -> policy -> exact consent -> execution gate -> Hermes ->
   Cua Driver. Prefer inspect-only capture; avoid typing.
6. Cleanly stop the runtime/driver session, run final sequential validation,
   finish report/progress evidence, commit, push, and verify clean remote
   equality.

## Validation checkpoint

Before pausing:

```text
Provision asset SHA/signature audit     passed (team YCK386LBJ7)
All JL-owned backend tests              103 passed
Ruff / ty / pip check                   passed
Swift format lint                       passed
Native deterministic tests             10 passed
Native release build/sign verification passed (ad-hoc limitation documented)
Hermes full suite                       intentionally not run
```
