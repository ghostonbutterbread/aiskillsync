# WSL / Windows managed copies — incomplete checkpoint

## Intent and ownership

Windows-native consumers cannot use Linux-only WSL symlink targets. This branch
was assigned an automatic Windows-mounted-destination copy projection with an
explicit per-destination override; other destinations must keep their existing
symlink behavior. Worktree: `/home/ryushe/worktrees/aiskillsync-wsl-windows-copy`.
Branch: `feat/wsl-windows-copy`; base: `04a78ec9564b4081d09b597fa0331da506115d97`
(`origin/beta` fetched at that commit); intended integration target: beta.
Parent owns review and integration; no merge, push, activation or nested review.

## Current checkpoint and evidence

**Not implemented; not review-ready or safe to integrate.** This checkpoint
contains a CLI-level RED test and the version bump required for any repository
commit. It does not contain an implementation commit. Refer to the current
feature tip for the checkpoint SHA; the parent receives that exact SHA in the
handoff. The test proposes `sync.copy_destinations: [windows]` as an override;
that spelling is a test-design choice, not yet an implemented or stable API.
The test runs the actual module entry point with a temporary config, HOME,
source and destination. The fixture is ordinary Linux, not WSL.

- Baseline before adding the test: `PYTHONPATH="$PWD" python3 -m unittest discover -s tests -q`: 243 tests, OK, skipped=1.
- Checkpoint: `PYTHONPATH="$PWD" python3 -m unittest discover -s tests -q`: 244 tests, **FAILED** (failures=1, skipped=1). `test_explicit_copy_destination_projects_nested_files` fails because the destination is a symlink, as on the existing implementation.
- Temporary isolated wheel build with `/usr/bin/python3 -m pip install --no-build-isolation --no-deps --target <scratch>/install <scratch>/src`; both the module `--version` and `importlib.metadata.version('aiskillsync')` returned 0.1.46. No user installation or config was changed.
- A preliminary creation-only prototype passed the first test but immediately failed on a second sync: a managed copy was classified as an unmanaged directory. The prototype was removed rather than checkpointing behavior that could mishandle adoption or existing links. No production copy behavior is present here.

## Design and unresolved correctness

The current sync planner classifies destination entries as symlinks or conflicts;
profile pruning records only link targets in `profile-projections.json`. Lane
switch collision checking and rollback are also link-specific. A safe design
must define durable per-entry ownership and file snapshots; refuse unowned or
locally changed trees and unexpected symlinks; preserve or explicitly back up
adopted data; and recover after interruptions without inferring ownership from
mere path/content coincidence. It must detect source/destination overlap before
mutating anything. Plan and apply must independently validate state. Destination
selection, profile pruning, filtered sync, multiple destinations, lane switching
and rollback need explicit treatment; just calling `copytree` is insufficient.

Automatic WSL/Windows mount detection, including mountinfo and nondefault mount
points where identifiable, and explicit override behavior are unimplemented.
A Linux fixture or simulated mountinfo cannot establish real WSL/Windows
interoperability. Real WSL validation remains required on an actual Windows
mounted destination, including Windows-side reading, updates, deletions, and
local-edit conflict handling. Native WSL is unavailable in this worktree.

## Exact resume point / gates

Start with `PYTHONPATH="$PWD" python3 -m unittest discover -s tests -p test_managed_copy.py -v`; confirm the expected RED, then implement the smallest safe vertical slice and confirm GREEN. Repeat RED/GREEN for automatic detection, refresh additions/changes/deletions, nested files, local modifications, unowned directories, unexpected symlinks, adoption, dry run, overlap, profile and filtered pruning, multi-destination, lane switch/rollback, and failure recovery. Run focused real CLI workflows and the full unfiltered unittest suite with this worktree first on `PYTHONPATH`. Update documentation and this dossier with exact evidence, limitations, and review status. The next repository commit must bump the patch version again in both version locations per `AGENTS.md`. Obtain the parent's independent review before beta integration. Remove this temporary dossier only during authorized integration.
