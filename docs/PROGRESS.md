# Phase 1 progress

Last updated: 2026-09-12 16:04 ICT

## Recovered state

- Branch: `main`, tracking `origin/main`; original history is intact.
- No installer, build, typecheck, or test process is currently running.
- Hermes is present as a clean Git submodule at
  `044a77b3b6af4ce16138d42762f812a20b9f7a89`; `.gitmodules` points to the
  official repository.
- Hermes, OpenJarvis, and PersonalJarvis source/license audits are complete.
  OpenJarvis and PersonalJarvis were inspected from temporary source snapshots;
  their dependencies were not installed.
- Component decisions, architecture, Capability Registry, Model Router, security
  model, third-party notice, configuration examples, and Phase 1 report are
  finalized.
- Hermes `0.21.2` was installed in a repository-local Python 3.13 environment
  with the optional groups used by upstream CI. Import, CLI help, dependency,
  YAML, shell syntax, secret-pattern, submodule, and blocking Ruff checks passed.
- Upstream `ty check` completed without diagnostics during the prior run, but
  its final exit code was not captured after the session handoff; a lightweight
  sequential rerun remains pending.
- The canonical per-file upstream suite was interrupted by the new execution
  rule after more than 13,000 passing tests. It had recorded two failures. One
  is confirmed as a macOS-only assumption in
  `tests/computer_use/test_cua_no_overlay.py`: the generic full-suite test mocks
  `/usr/bin/cua-driver` but the Darwin branch correctly requires a real signed
  `CuaDriver.app`. The second failure output was truncated and must be recovered
  with a narrow rerun or documented as unknown; the full heavy suite will not be
  restarted while resources are constrained.

## Resource state and cleanup decision

- The external project volume had only about 4.6 GiB free after installation.
- `.venv` occupied about 23 GiB of allocated space and the Hermes checkout about
  24 GiB. The unusually high allocation is amplified by the volume's allocation
  behavior across many small Python/source files.
- System load was still decaying after the interrupted suite; no matching heavy
  process remained.
- Safe cleanup is complete. Only reproducible ignored state was removed:
  `.venv`, `.jl-agent`, and bytecode/test/lint caches inside the Hermes
  checkout. Source, documentation, Git history, and the pinned submodule were
  preserved. Free space increased from about 4.6 GiB to about 90 GiB; the
  submodule remains clean.

## Sequential phase status

| Step | Status | Evidence / next action |
|---|---|---|
| 0 — Recover current state | COMPLETE | State recovered, generated artifacts cleaned, resources rechecked, source preserved. |
| 1 — Hermes audit | COMPLETE | Recorded in `COMPONENT_MATRIX.md` and `PHASE1_REPORT.md`; representative source paths revalidated after recovery. |
| 2 — OpenJarvis audit | COMPLETE | Existing snapshot commit and cited registry/routing/Apple FM paths revalidated; no dependency installation. |
| 3 — PersonalJarvis audit | COMPLETE | Existing snapshot commit and cited voice/macOS/computer-use/safety paths revalidated; no dependency installation. |
| 4 — Final component decisions | COMPLETE | All 34 matrix rows use a valid decision class; single-core architecture, six security classes, registry/router contracts, and no-SwiftUI boundary are consistency-checked. |
| 5 — Prepare baseline structure | COMPLETE | Pinned clean submodule, JL-owned boundaries, bootstrap/config files, shell/YAML checks, and no-SwiftUI rule validated. |
| 6 — Install/validate Hermes baseline | COMPLETE | Prior CI-extras installation, `pip check`, imports, CLI help, Ruff, and partial canonical suite are documented; arm64/macOS/Python compatibility revalidated statically. Generated environment was then removed to recover disk space. |
| 7 — Fix baseline issues | COMPLETE | CI extras were added to dev bootstrap, pip cache and verification HOME are project-local, official submodule URL restored, and expensive-suite coverage gaps documented without overclaiming. |
| 8 — Final Phase 1 review | COMPLETE | Documentation/diff/secret/generated-file reviews passed; baseline and documentation are committed logically. |

## Next action

Phase 1 is complete. Begin Phase 2 only from the ordered scope in
`PHASE1_REPORT.md`. Do not treat the partial ~41,000-test run as a pass; rerun
the supported Linux full lane and official macOS-only lane before changing the
Hermes pin.
