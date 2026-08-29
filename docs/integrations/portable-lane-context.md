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
- This change does not activate a lane, automatically modify the user's
  aiskillsync config, or add a user-facing CLI command.
- Optional BBH launcher projection is bound to one explicit local lane group;
  switching any other group must not create, remove, or repoint its launcher.

## Base and target

- Feature branch: `feat/portable-lane-context`
- Base: `main` at `8dea009`
- Intended integration target: `main`
- Companion BBH branch: `fix/portable-lane-context`, target `beta`.

## Evidence

- Focused test: `python3 -m unittest tests.test_lanes.LaneCliTests.test_switch_writes_resolved_runtime_context -v`
- Isolation regression: `python3 -m unittest tests.test_lanes.LaneCliTests.test_switching_unrelated_lane_does_not_repoint_bbh_launcher -v`
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
   Aiskillsync's optional `bbh_launcher_path` must be paired with
   `bbh_launcher_lane_group`; only that group's switch may repoint it.
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

## Final migration plan

### Command contract

Use one generic dispatcher contract for BBH-owned executable code:

```bash
bbh agents/<tool>.py <arguments>
bbh scripts/<tool>.py <arguments>
bbh skills/<skill>/scripts/<tool>.py <arguments>
```

The repository-relative target identifies the tool; it is not a checkout path.
`bbh` resolves that target beneath the selected checkout and runs Python through
that checkout's local environment. Do not add a named-tool registry in this
migration: it would create a second mapping to maintain. A small alias layer may
be proposed later only for proven high-frequency stable commands.

### Phases

1. **Finish the runtime boundary.** Keep `scripts/bbh` as the sole public
   BBH executable dispatcher. Route `run_zdt.sh` through it. Keep internal
   Python imports/subprocesses self-relative when they are not user/operator
   launch boundaries.
2. **Make lane activation explicit.** Aiskillsync selects skill projections;
   the selected agent profile must expose exactly one lane-owned `bbh` on PATH.
   Use separate stable/beta command roots or profiles and reject installation
   that would overwrite an existing launcher from another lane.
3. **Migrate canonical agent-visible commands.** In canonical `skills/`,
   `prompts/`, README/docs, nested runnable references, and generated handoff
   text, replace direct BBH `python`, CWD, root-variable, and absolute-checkout
   invocations with the generic `bbh` contract. Preserve external data paths as
   command arguments.
4. **Regenerate skill projections/mirrors.** Treat canonical skill source as
   authoritative. Rebuild or remove stale tracked provider mirrors rather than
   maintaining separate command text in `.agents/` or `.claude/`. Then run an
   aiskillsync dry-run and fresh-agent read-only resolution check.
5. **Remove implicit external code discovery.** BBH must not choose optional
   code from conventional sibling/home paths. Declare a checkout-local
   dependency, explicit plugin, or narrowly owned configuration instead.
6. **Close remote and generated-command gaps.** Remote BBH work must preflight
   the selected remote launcher/root/revision; generated next-command/help text
   must emit `bbh <repository-relative-target>`, not direct Python.
7. **Enforce and verify.** Add static checks for every agent-visible runnable
   document, including multiline CWD/PYTHONPATH patterns and mirrors. Verify all
   documented targets with `bbh --print-command`; test beta/stable launcher
   isolation, the local virtualenv path, and a fresh synchronized skill
   projection.

### Explicit non-goals

- Do not hard-code a selected BBH checkout in skills or Aiskillsync receipts.
- Do not use Aiskillsync as a general dependency/source-path resolver.
- Do not convert external artifact roots (`/mnt/bounty`, Shared, temporary
  files, source inputs) into BBH paths; retain them as resolver/configuration
  values.
- Do not require a Bounty Core commit pin for this migration.

### Activation evidence and remaining boundary

A disposable `portable-smoke` profile was projected to temporary Hermes and
Codex destinations from the candidate BBH worktree. Aiskillsync created 89
symlinks in each destination, each resolving into that candidate's `skills/`
tree; a repeat dry-run reported only correct-source no-ops. A Codex read-only
smoke then read the projected XSS and Chromium Test skills and correctly
explained the `bbh <repository-relative-target>` contract.

This proves the synchronizer/projection path. It does not activate the candidate
for ordinary agents: the active `security` profile remains deliberately bound
to the configured BBH beta checkout until this branch is reviewed, merged into
that lane, and the profile is re-synced.

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
