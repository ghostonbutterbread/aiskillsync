# Portable lane context

## Status

Implementation in progress on `feat/portable-lane-context`.

## Goal

After a successful aiskillsync lane switch, atomically publish one host-local
runtime receipt that contains both the selected logical lanes and their resolved
checkouts, configured branches, and Git revisions. This lets optional
cross-repository integrations use the selected lane without embedding host
checkout paths in shared skills or code.

## Boundaries

- Aiskillsync owns the generated receipt at `active-stack.json` next to its local
  configuration.
- The receipt is not shared configuration and is not committed by consumers.
- Applications remain standalone; consuming the receipt is only for a deliberate
  optional integration.
- This change does not activate a lane, modify the user's aiskillsync config, or
  change the installed CLI.

## Base and target

- Feature branch: `feat/portable-lane-context`
- Base: `main` at `8dea009`
- Intended integration target: `main`
- Companion BBH branch: `fix/portable-lane-context`, target `beta`.

## Evidence

- Focused test: `python3 -m unittest tests.test_lanes.LaneCliTests.test_switch_writes_resolved_runtime_context -v`
- Full suite: `python3 -m unittest discover -s tests -v`
- Isolated BBH stable/beta switch: temporary config verified both the projected
  `ssrf` skill symlink and the selected root/revision in `active-stack.json`.

## BBH portability gates before activation

The companion BBH branch has a sound self-resolving Python dispatcher, but it
is not yet safe to activate as a universal portable runtime. These are BBH or
host-activation fixes; aiskillsync records the gate and must not compensate by
selecting BBH source paths for skills or dependencies.

1. **Lane-local command ownership:** BBH setup currently installs `bbh`,
   `tool-run`, and `recon-bus` into one shared `~/.local/bin` by default. A
   stable install can overwrite beta (and vice versa). Require distinct
   profile/PATH command roots or another explicit lane-owned launcher mapping;
   reject shared-path clobbering and test both installation orders.
2. **Every BBH entrypoint uses the dispatcher:** replace `run_zdt.sh` ambient
   `python3`/CWD bootstrap with the selected checkout's `scripts/bbh`. Add an
   end-to-end test that proves it imports that checkout's local dependency
   environment.
3. **Remote dispatch is selected and verified:** Recon-Ry promotion must not
   invoke bare remote `bbh` from `PATH`. The remote activation contract must
   provide the selected launcher and preflight its resolved root/revision before
   starting background work.
4. **Canonical runnable instructions and mirrors migrate together:** remove
   direct BBH checkout paths, `HARNESS_ROOT` repository-code selection, CWD plus
   `PYTHONPATH` bootstraps, and direct Python commands from canonical skills,
   prompts, nested runnable references, README/docs, and tracked provider
   mirrors. Runnable BBH commands use `bbh <repository-relative-target> ...`.
   Reference-only links remain relative to the active skill/check-out.
5. **Regression coverage matches the contract:** scan all runnable documents,
   including provider mirrors and nested references, and detect multiline
   CWD/`PYTHONPATH`/interpreter command blocks. Test same-name stable/beta
   launcher installation collision, remote selected-launcher validation, and
   shell entrypoints using the checkout-local interpreter.
6. **Optional external code is explicit:** BBH must not silently load MGP, AI
   Policies, Hunter Memory, or other repositories from conventional home paths.
   Make each an explicit optional plugin/configuration or a declared dependency.

### Accepted Bounty Core dependency policy

BBH installs Bounty Core into its checkout-local virtual environment from the
normal upstream repository and deliberately refreshes it during setup. That is
owner-approved: do not require a commit pin for this migration. Setup and
runtime diagnostics should still report the resolved Bounty Core revision so a
run has provenance. This is an accepted non-reproducibility trade-off, not a
reason for aiskillsync to select a sibling or global Bounty Core checkout.

## Activation boundary

After review and integration, release the new aiskillsync version, install or
upgrade it on the host, then run the desired lane switch. Existing running agent
processes must be restarted or relaunched before they can consume a new runtime
context. Do not activate the portable BBH lane until the BBH portability gates
above are complete and verified in the selected stable and beta command roots.
