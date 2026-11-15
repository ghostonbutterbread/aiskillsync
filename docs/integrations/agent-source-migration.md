# Agent source migration integration dossier

- **Status:** feature handoff; independent review pending
- **Owner:** delegated Hermes implementer; parent owns review and integration
- **Branch / worktree:** `feat/agent-source-migration` / `/home/ryushe/worktrees/aiskillsync-agent-source-migration`
- **Base commit:** `78b660dc69b10d5e36ed702c5aa50d835b47b6cf`
- **Intended integration target:** `beta`
- **Last updated:** 2026-11-15
- **Owning feature branch/ref:** `feat/agent-source-migration`
- **Latest immutable recovery checkpoint:** `078b8a9e6a80356177f73d905a863e5bfbc28c13` (implementation; followed by version and dossier handoff commit)
- **Feature implementation commit(s):** `078b8a9e6a80356177f73d905a863e5bfbc28c13`
- **Inspiration / reference:** explicit request for generic former-source migration; no Hoster state or user config edits authorized.

## Intent and implemented contract

Support optional validated OMP model overlays and explicit `agents --former-agent-source BRIDGE=ABSOLUTE_DIR` migration. Require matching former source path identity, native provider, filename and role; exact old-source rendering using supplied overlays; and an original marker corresponding to a subset of those overlays. Preserve displaced bytes and receipts. Unknown edits and unrelated stale files block; `--adopt` does not override migration conflicts. Existing default stale-owner behavior is unchanged. Protect configured and former source roots and check snapshot and source races; incomplete applies require manual reconciliation. This is not a transactional filesystem or a concurrency lock.

## Evidence and review

- TDD red then green: OMP model rejected as unsupported before validation change; migration API missing before implementation; CLI option unrecognized before adding it; overlapping sources, unknown native adoption, invalid marker, added stale entry and source-publication race each failed before their respective fixes.
- `PYTHONPATH="$PWD" python3 -m unittest discover -s tests -p test_agent_source_migration.py -v`: 18 passed, includes real CLI dry-run/apply, model validation/config roundtrip, old modified marker, source overlap, races, backups and partial receipt, native providers and idempotence.
- `PYTHONPATH="$PWD" python3 -m unittest discover -s tests -p test_agent_refresh.py -v`: 30 passed.
- `PYTHONPATH="$PWD" python3 -m unittest discover -s tests`: 305 run, 1 skipped, otherwise passed.
- At implementation checkpoint, `python3 -m aiskillsync --version` and isolated `/usr/bin/python3 -m pip install --no-deps --no-build-isolation --target ... .` with package metadata query: 0.1.54. After the required handoff version bump, the isolated install, CLI and package metadata all returned 0.1.55; focused and full suites were rerun with the final version.
- **Independent review:** pending, assigned to parent; specifically review source/path race boundaries, stale-owner scans, subset marker verification, exact comparison, backup/incomplete semantics, and Hoster's reviewed file layout against renderer.
- **Merge/ancestry evidence:** base equals fetched `origin/beta`; no merge or push authorized.

## Blockers and deferred work

- Independent exact-diff review is required before beta integration; parent owns it. Re-run focused and full tests after corrections and perform beta merge preflight.
- Hoster's actual old sources and 12 roles are not included in this fixture. After separate user authorization and independent review, the parent must preview against a safe copy of those exact contents, verify the 3 edited bodies match generated overlay formatting, then review dry-run output before any live operation. No runtime activation is authorized by this branch.
- Destination/source reparenting and late writes cannot be fully prevented; stop consumers and concurrent writers and use backup receipts for partial failure recovery.
- Project Kanban CLI reports delegate child contexts cannot mutate boards; parent owns that coordination.

## Interruption / resume handoff

- **Owning feature branch/ref:** `feat/agent-source-migration`
- **Latest immutable recovery checkpoint / implementation:** `078b8a9e6a80356177f73d905a863e5bfbc28c13`; branch tip also contains a later dossier/version-only handoff commit.
- **Exact resume point:** parent independently reviews the full base-to-tip diff, verifies tests and migration fixture against reviewed source content, and decides beta integration; no push or activation has occurred.
- **Working-tree state:** clean after handoff commit, except ignored Python build/cache artifacts.

## Decision gates

- **Integration:** parent independently reviews exact final branch diff, checks tests and beta compatibility, removes this branch-local dossier during integration.
- **Activation:** separate operator authorization after non-mutating preview and recovery review.
- **Promotion:** separate explicit approval.

## Decision record

- 2026-11-15 — isolated feature started from fetched beta; no merge, push, user config or Hoster modifications.
