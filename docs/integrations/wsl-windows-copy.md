# WSL / Windows managed copies

## Intent and integration

Windows-native consumers cannot use WSL-only symlink targets. The feature adds
per-destination copy projection on detected WSL Windows mounts or explicit
`sync.copy_destinations`; other skill destinations retain symlinks. Feature
worktree: `/home/ryushe/worktrees/aiskillsync-wsl-windows-copy`; branch
`feat/wsl-windows-copy`; base `04a78ec9564b4081d09b597fa0331da506115d97`;
target beta. Parent owns review and integration; no push, merge or activation.
RED checkpoint `5e684b4963ccd22d5ddfb41c9742cc2ada8cb992` precedes this
implementation. Current branch tip after this dossier is committed is the
implementation commit; the exact SHA is supplied in the handoff.

## Implemented contract

Per-copy ownership records alongside the selected config store absolute source,
destination, profile and content/directory snapshots. Plan and apply compare
snapshots; unchanged managed copies refresh, selected stale copies prune,
unchanged projections are no-ops, and edited/unowned trees are protected.
Existing correctly targeted symlinks migrate. Nested regular files and empty
directories, dry-run, explicit destination override and profiles are supported.
Copy destination/source overlap is rejected. Staged refresh preserves a sibling
backup if record writing fails; multiple actions are not transactional.

## Evidence and dependency boundary

- CLI RED at checkpoint: expected symlink instead of directory; test then green.
- `PYTHONPATH="$PWD" python3 -m unittest discover -s tests -p test_managed_copy.py -v`: 11 passed. Exercises real module CLI, nested files, no-op, refresh/deletion, local edits, unmanaged protection, dry run, legacy/profile migration, profile filtering, overlap, lane switch block, mocked mount selection, apply revalidation, record-write failure recovery, and unreadable-directory handling.
- With sibling's untracked mount module and tests in this shared worktree:
  `PYTHONPATH="$PWD" python3 -m unittest discover -s tests -p test_windows_mount.py -v`: 17 passed; full `python3 -m unittest discover -s tests -q`: 271 passed, one skipped.
- Source CLI and isolated scratch installation metadata both report 0.1.47.
- **Required dependency not in this commit:** `aiskillsync/windows_mount.py`
  and `tests/test_windows_mount.py` are owned by the concurrent sibling agent
  and intentionally unstaged. Parent must add/commit the sibling's finished
  module and tests before using or reviewing the combined feature; this commit
  alone will fail imports. Rerun focused and full tests on the combined tip.

## Limitations and next step

Read README's WSL section for the exact user-facing recovery and unsupported
paths. Lane switch to copy destinations, source/profile/mode changes, nested
symlinks and special files are deliberately blocked. Pruning does not cover old
unselected destination roots or unavailable/disabled source roots. Snapshots
track file contents and tree shape, not permission/timestamp-only changes.
Concurrent writers are not locked; stop consumers that edit destination trees
while syncing. Filesystem/manifest failures can leave partial application and a
recoverable sibling backup or an unowned directory.

Real WSL Windows-mounted filesystem interoperability, Windows-side reading,
refresh/prune and local-edit preservation remain unverified: this host is Linux,
not WSL. Trigger an actual WSL test on a Windows-backed mount before activation;
run `sync all --dry-run`, real sync, Windows-side reads, source add/change/delete,
repeat sync, and local-edit conflict checks with a disposable config and tree.
Parent: collect the sibling module, review complete diff and failure semantics,
verify combined tests, then choose integration/activation separately. Remove this
branch-local dossier during authorized integration.
