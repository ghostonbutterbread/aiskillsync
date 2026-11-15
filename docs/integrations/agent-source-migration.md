# Agent source migration integration dossier

- **Status:** bounded-memory correction complete; parent independent re-review pending
- **Owner:** delegated Hermes implementer; parent owns review and integration
- **Branch / worktree:** `feat/agent-source-migration` / `/home/ryushe/worktrees/aiskillsync-agent-source-migration`
- **Base commit:** `78b660dc69b10d5e36ed702c5aa50d835b47b6cf`
- **Intended integration target:** `beta`
- **Last updated:** 2026-11-15
- **Owning feature branch/ref:** `feat/agent-source-migration`
- **Latest prior immutable recovery checkpoint:** `19f60b5554df5559c1d8a37e54c53a6ef6d1feb2` (the bounded-memory correction and this dossier are in the subsequent single commit)
- **Feature implementation commit(s):** `078b8a9e6a80356177f73d905a863e5bfbc28c13`
- **Inspiration / reference:** explicit request for generic former-source migration; no Hoster state or user config edits authorized.

## Intent and implemented contract

Support optional validated OMP model overlays and explicit `agents --former-agent-source BRIDGE=ABSOLUTE_DIR` migration. Require matching former source path identity, native provider, filename and role; exact old-source rendering using supplied overlays; and an original marker corresponding to a subset of those overlays. Preserve displaced bytes and receipts. Unknown edits and unrelated stale files block; `--adopt` does not override migration conflicts. Existing default stale-owner behavior is unchanged. Protect configured and former source roots and check snapshot and source races; incomplete applies require manual reconciliation. This is not a transactional filesystem or a concurrency lock.

For unformatted migrations, explicit adoption requires an exact link to the matching regular former source and matching role metadata, with both old and new source bytes tracked and link text backed up. Unselected former-source links block. Migration CLI neither clones nor refreshes any selected source in preview or apply; all source agent directories must exist and configured branch checks must pass. Refresh separately and re-preview. A preview is not a persisted authorization token and does not lock the filesystem.

## Evidence and review

- TDD red then green: OMP model rejected as unsupported before validation change; migration API missing before implementation; CLI option unrecognized before adding it; overlapping sources, unknown native adoption, invalid marker, added stale entry and source-publication race each failed before their respective fixes.
- `PYTHONPATH="$PWD" python3 -m unittest discover -s tests -p test_agent_source_migration.py -v`: 18 passed, includes real CLI dry-run/apply, model validation/config roundtrip, old modified marker, source overlap, races, backups and partial receipt, native providers and idempotence.
- `PYTHONPATH="$PWD" python3 -m unittest discover -s tests -p test_agent_refresh.py -v`: 30 passed.
- `PYTHONPATH="$PWD" python3 -m unittest discover -s tests`: 305 run, 1 skipped, otherwise passed.
- At implementation checkpoint, `python3 -m aiskillsync --version` and isolated `/usr/bin/python3 -m pip install --no-deps --no-build-isolation --target ... .` with package metadata query: 0.1.54. After the required handoff version bump, the isolated install, CLI and package metadata all returned 0.1.55; focused and full suites were rerun with the final version.
- **Independent review:** pending, assigned to parent; specifically review source/path race boundaries, stale-owner scans, subset marker verification, exact comparison, backup/incomplete semantics, and Hoster's reviewed file layout against renderer.
- **Merge/ancestry evidence:** base equals fetched `origin/beta`; no merge or push authorized.

### Five-blocker correction receipts

- Before correction, each new regression was run independently and failed on the intended assertion: raw `--adopt` accepted a local file; real CLI pulled unreviewed source bytes after preview; a missing checkout was planned for cloning; a relative former-source argument cloned before failing; an unselected dangling former link did not block; and a write at `os.link` returned success. An additional raw source-publication race also returned success before correction. All corresponding tests now pass in `tests/test_agent_source_migration.py`.
- Focused migration suite: 25 passed; agent-refresh suite: 30 passed. Full isolated suite: 312 run, 1 skipped (`OPENCODE_TEST_BIN` not supplied), otherwise passed. Expected simulated rollback diagnostics appear in full-suite output.
- Isolated package installation and CLI both report 0.1.56. `git diff --check` passes. The full suite includes nonmigration clone/pull and profile tests.
- Parent must independently review the full corrected branch diff, including exact former-link validation, selected/unselected link scans, preview/apply operator boundary, and publication/receipt race checks. These fixes are not an accepted review or deployment.

### Second independent-review correction receipts

- Raw migration `skip` and `link` now fingerprint current source bytes, rechecking preflight and final verification; a post-plan edit blocks without altering the destination, while a publication-time edit yields an incomplete apply. An initially absent migration destination is bound to an empty discovery snapshot on creation; stale former-source links of any suffix block or invalidate apply.
- Before completion, every adoption/refresh receipt verifies the retained snapshot and displaced original recursively, including inode and bytes or symlink text; checks repeat around receipt finalization. On a mismatch, the failure identifies which retained copy was verified at that instant. This includes nonmigration adoption receipts; unchanged directory and symlink adoption remain supported. These checks do not lock the filesystem and cannot prevent later writes or moves.
- RED on `cb56f42b8e90849a2d369fbf6ac63fe785a4b0d2`: raw skip/link edits and publication edit returned success; backup corruption and replacement, displaced corruption, missing-root stale link, skip mutation during another publication, and already-present non-markdown former link each failed their new expected-error/blocker assertions. Non-markdown insertion was also observed RED against the intermediate correction. Green controls cover unchanged raw link/skip, existing migration backups, and ordinary adoption.
- Final isolated per-command `HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, and worktree-first `PYTHONPATH`: migration suite 35 passed; refresh suite 30 passed; full suite 322 run, 1 skipped (`OPENCODE_TEST_BIN` not supplied), otherwise passed. Full suite prints expected simulated rollback diagnostics. Isolated install, package metadata and CLI report 0.1.57. No persistent shell exports were used.
- Parent must re-review the exact correction and full feature, including backup/inode checks, root snapshots and manual recovery semantics; no merge, push, Hoster access, or activation performed.

### Bounded backup-evidence correction

- Independent review found `19f60b5` retained all bytes in both evidence trees even for ordinary directory adoption. TDD RED on that commit: real nonmigration apply of 32 × 256 KiB files with a symlink peaked at **24.20 MiB** traced allocations, failing the **2 MiB** limit. With streaming SHA-256 evidence and 128 KiB copying, the same test measured **0.53–0.54 MiB**. This is tracemalloc during apply, not RSS or a guarantee for arbitrary tree shape.
- Content evidence is SHA-256 (collision resistance, not mathematical byte equality), with inode/entry state and no-follow stable fd checks; link text remains literal. Tests exercise same-size/mtime-restored nested corruption and same-content inode replacement in both snapshot and displaced trees, corruption before displacement, fd-open swaps, mid-read edits and dangling links. Existing migration/race controls remain intact; the concurrent snapshot creation test now hooks exclusive `os.open` and asserts it fired.
- Bounds: content buffering is independent of total directory file bytes, but evidence and directory listings grow with entry count and recursion depth; SHA-256 requires repeated full-tree reads around receipt finalization. Existing direct regular-file planning/comparison and migration snapshot paths can still retain whole individual files in memory; this change does not claim a global constant-memory apply. Filesystem races and post-verification mutation remain possible; stop concurrent writers.
- Final isolated per-command environment with worktree-first `PYTHONPATH`: migration 35 passed; refresh 35 passed; agent suite 80 run, 1 skipped; full suite 327 run, 1 skipped (`OPENCODE_TEST_BIN` absent), otherwise passed. Full suite emits expected simulated rollback diagnostics. The first full run exposed a stale mock hook after the copy implementation changed; it was retargeted to the actual exclusive open, asserted fired, and full suite reran green. Isolated wheel install, CLI and package metadata report 0.1.58. No persistent environment exports.
- Parent must re-review the complete feature and this correction, including content-hash assumptions and memory limits. The remote fixture export remains blocked; do not attempt or route around it. No Hoster access, merge, push or deployment was performed.

## Blockers and deferred work

- Independent exact-diff review is required before beta integration; parent owns it. Re-run focused and full tests after corrections and perform beta merge preflight.
- Hoster's actual old sources and 12 roles are not included in this fixture. After separate user authorization and independent review, the parent must preview against a safe copy of those exact contents, verify the 3 edited bodies match generated overlay formatting, then review dry-run output before any live operation. No runtime activation is authorized by this branch.
- Destination/source reparenting and late writes cannot be fully prevented; stop consumers and concurrent writers and use backup receipts for partial failure recovery.
- Project Kanban CLI reports delegate child contexts cannot mutate boards; parent owns that coordination.

## Interruption / resume handoff

- **Owning feature branch/ref:** `feat/agent-source-migration`
- **Latest prior immutable recovery checkpoint:** `19f60b5554df5559c1d8a37e54c53a6ef6d1feb2`; this dossier accompanies the subsequent bounded-memory correction commit. Inspect branch tip for its immutable ID.
- **Exact resume point:** parent independently reviews the full base-to-tip diff, verifies tests and migration fixture against reviewed source content, and decides beta integration; no push or activation has occurred.
- **Working-tree state:** clean after handoff commit, except ignored Python build/cache artifacts.

## Decision gates

- **Integration:** parent independently reviews exact final branch diff, checks tests and beta compatibility, removes this branch-local dossier during integration.
- **Activation:** separate operator authorization after non-mutating preview and recovery review.
- **Promotion:** separate explicit approval.

## Decision record

- 2026-11-15 — isolated feature started from fetched beta; no merge, push, user config or Hoster modifications.
