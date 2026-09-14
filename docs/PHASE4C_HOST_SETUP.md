# Phase 4C host setup

## Safety boundary

JL never grants, resets, edits, or bypasses macOS TCC. Accessibility and Screen
Recording belong to the official `com.trycua.driver` identity and must be
enabled by the user in System Settings. Do not grant either permission to the
JL SwiftUI app, Terminal, or the Python runtime for this architecture.

## Reviewed driver version

Phase 4C selects the published stable release `cua-driver-rs-v0.28.0`, source
tag commit `1b50c02e2d34734f64d2d22f54eb76cc97b4a663`. It satisfies the pinned
Hermes minimum manifest contract (`>=0.20.0`). The provisioning script records
the release-published SHA-256 values for:

- `install.sh`
- `_install-rust.sh`
- `cua-driver-rs-0.28.0-darwin-universal.tar.gz`

The script downloads each asset as a file and verifies it before any execution.
It does not use a curl-to-shell pipeline. It also verifies the staged app's
deep code signature, exact bundle ID `com.trycua.driver`, and an official team
ID accepted by pinned Hermes.

## Repository-only preflight

From the repository root:

```bash
./scripts/provision-cua-driver.sh --audit
```

This downloads into a temporary directory inside the repository, validates the
assets, removes that exact temporary directory, and performs no installation,
LaunchServices registration, driver startup, TCC prompt, or permission change.

## Host installation boundary

The following command is intentionally not run automatically during Phase 4C
repository work:

```bash
./scripts/provision-cua-driver.sh --install
```

It uses the reviewed official installer with the exact version pin, disables
shell-profile edits, retains only two release versions, and then validates:

- `/Applications/CuaDriver.app` exists and has the expected signature;
- bundle ID and team identity match the audited contract;
- the installed binary returns a compatible manifest;
- permission status can be read without granting anything.

The upstream installer writes outside this repository and registers the app
with LaunchServices. Run it only after reviewing this document and the script.
It does not grant TCC.

## Manual TCC action

After installation, first inspect readiness without requesting prompts:

```bash
/Applications/CuaDriver.app/Contents/MacOS/cua-driver permissions status --json
```

If Accessibility or Screen Recording is not granted, stop automation work.
Use System Settings -> Privacy & Security to add/enable **CuaDriver** for:

1. Accessibility
2. Screen & System Audio Recording (shown as Screen Recording on older macOS)

Do not add Input Monitoring. Do not grant these permissions to JL Agent or the
Python runtime. Return to JL Agent and choose Recheck only after both user
decisions are complete. Relaunch CuaDriver and the foreground JL runtime only
if readiness reports that a restart is required.

## Foreground JL runtime

Use the repository-owned entry point after creating the small `.venv` described
by `pyproject.toml`:

```bash
./scripts/run-local-runtime.sh
```

In a second terminal, inspect lifecycle metadata without starting another
runtime:

```bash
./scripts/run-local-runtime.sh --status
```

The native app connects to this foreground process. It does not guess a Python
path or silently spawn a child. An active socket rejects duplicate ownership;
after a crash, the next legitimate runtime recovers only an owned stale socket.
Stop with Control-C for graceful socket/readiness cleanup.

## JL development signing

The app bundle ID is `com.jlagent.control`. When an existing Apple Development
identity is available, pass its exact name or SHA-1 to the build:

```bash
JL_CODE_SIGN_IDENTITY='Apple Development: Name (TEAMID)' \
  ./macos-app/Scripts/build-app.sh
```

Without that environment variable the build is ad-hoc signed and prints the
development limitation. Do not create a certificate or change Keychain trust
merely to silence that warning. Cua TCC remains attached to the separately
signed `com.trycua.driver` app.

## Revocation

Revoke access in the same two System Settings panels. JL treats denied,
unknown, unavailable, or restart-pending state as not ready. No client-side
control can override the runtime decision.
