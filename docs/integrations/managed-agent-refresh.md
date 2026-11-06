# Managed agent refresh — blocked fd-bound correction

- **Branch/worktree:** `feat/managed-agent-refresh`, `/home/ryushe/worktrees/aiskillsync-managed-agent-refresh`.
- **Base and intended handoff:** `da2c6e8` on `feat/omp-agent-overlays`; parent integration target `beta` (`origin/beta` fetched at `850e6819e5ba9c21b8f07ab80b1d547844810f3a`). This defect also affects raw adoption in earlier descendants. The separately owned `fix/agent-overlay-parser-compat` worktree is out of scope; no parser files were changed in this checkpoint.
- **Prior reachable checkpoint:** `3044b91597ef5e98c6124a3f5ec46c52804d1213`; this follow-up is the immediately subsequent feature-branch commit. Version `0.1.42` in both version files. No push, merge, activation or Hoster work.

## Decision: NOT READY — concrete remaining blocker

The two prior reviewer root-swap regressions now pass, but a newly demonstrated, stronger root-relocation race remains red:

```
HOME=<isolated> XDG_CONFIG_HOME=<isolated> XDG_CACHE_HOME=<isolated> TMPDIR=<isolated> PYTHONPATH="$PWD:$PWD/tests" \
  python -m unittest test_agent_refresh.AgentRefreshTests.test_relocated_bound_root_under_source_at_publication_is_not_written -v
```

The fixture moves the *already opened destination directory itself* under a protected source root and replaces its old name with a symlink to that source, exactly when `os.link` publishes. The link is descriptor-relative and does not follow the replacement symlink, but its held directory inode now resides under the protected source. It consequently creates `skills/moved/reviewer.md` before the post-publication check reports incomplete. The original protected source entries are not overwritten, but this violates the stated stronger condition that the agent must not write anywhere under a protected source during a concurrent root swap. A final pathname/ancestry check cannot atomically prevent an uncooperative process from reparenting the inode between the check and a write through its descriptor; advisory locks cannot enforce cooperation. Meeting this stronger condition needs an enforceable restriction on renames/reparenting during apply or an explicit decision narrowing the contract to preserving the original protected inodes and never following the replacement. Do not treat the existing fd-bound implementation as satisfying the stronger condition. The exact resume point is this red regression and a design/review decision on that constraint.

## Implemented candidate and boundaries

- Existing roots are opened and their `fstat` identity and actual source separation are checked during planning and at apply. Apply holds directory descriptors and uses descriptor-relative stat, reads, creation, exclusive publication, snapshots, displaced backups and receipts. Missing roots are built with no-follow directory traversal; configured symlink roots remain supported. The planned backup parent is opened independently, not by traversing `..` through a destination that can have been moved.
- Destination originals get an independent snapshot and a same-filesystem private displaced inode. Exclusive `renameat2(RENAME_NOREPLACE)` on Linux and `renameatx_np(RENAME_EXCL)` on Darwin prevent clobbering an independently occupied displacement slot; unsupported platforms/filesystems fail rather than falling back to unsafe rename. The Linux path was exercised here; Darwin and real cross-mount cases require actual-platform verification. Backups inside selected discovery roots are refused; an unavailable writable same-filesystem ancestor outside discovery blocks adoption/refresh.
- Receipts remain incomplete through the actions, all bound roots are rechecked before and after completing receipts, and detected later failures attempt to mark completed receipts incomplete again. If that repair itself fails, the error explicitly says so; receipt writes and multi-action apply are not power-loss transactional. An open descriptor can still change the displaced inode after return. Unrelated direct mutation of private backup directories remains outside the prior contract.
- OMP, OpenCode and Codex native refresh, raw link adoption, explicitly adopted local/legacy entries, exact source-byte digest, planned ownership snapshots and late-writer preservation remain in scope. No runtime activation is implied.

## Evidence

- Initial isolated baseline: `test_agent_refresh.py` ran 15 tests with the two root-swap failures. Both now pass, using the new exclusive-displacement hook for the cross-process displacement race.
- Added syscall-boundary coverage for native/raw create, native/raw/legacy adoption, refresh, receipt completion, independent backup-slot creation, a backup-directory symlink swap, missing-root creation, configured root symlinks, unsupported fd/rename primitives, a previously completed root swapped during a later action, nested destination backup placement and relocation before backup-parent binding. Existing tests cover active local edits, exact CRLF source digests, old writable fds and abrupt process exit.
- Latest isolated full suite: `python -m unittest discover -s tests -q` **219 tests, 1 failure, 1 optional skip**. The sole failure is the stronger relocation regression above. Before adding that regression, the same candidate ran 218 tests with 1 optional skip and no failures; that is not a release receipt.
- Source CLI `python -m aiskillsync --version` reported `0.1.42`; an isolated copied-source install of this candidate reported both CLI and package metadata `0.1.42`. Independent review, actual macOS verification, real cross-mount verification and the separately owned parser reconciliation remain for the parent. No push, merge or activation.
