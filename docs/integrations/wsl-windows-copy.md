# WSL / Windows managed copies

## Intent and integration

Windows-native consumers cannot use WSL-only symlink targets. This feature adds
per-destination copy projection on detected WSL Windows mounts or explicit
`sync.copy_destinations`; other skill destinations retain symlinks. Worktree:
`/home/ryushe/worktrees/aiskillsync-wsl-windows-copy`, branch
`feat/wsl-windows-copy`; base `04a78ec9564b4081d09b597fa0331da506115d97`,
integration target `beta`. Parent owns independent review and integration; the
feature branch has been pushed but not merged or activated. RED checkpoint `5e684b4963ccd22d5ddfb41c9742cc2ada8cb992`;
implementation checkpoint `aacdc9ce0ccb7d855bf1c86db0accdb84ab97d38`;
Earlier checkpoints added the detector and addressed independent review blockers.
Independent reviews accepted the final implementation; real WSL and consumer
discovery evidence is recorded below. Next: parent-controlled beta integration.
Remove this branch-local dossier during the integration operation.

## Contract and corrections

Per-copy records alongside the config bind source, destination, profile, source
snapshot, and actual destination snapshot. Source snapshots include file execute
bits so mode-only changes refresh; destination snapshots protect local content,
tree, and mode edits. Older 0.1.48 single-snapshot records remain compatible.
Copies recorded by unreleased 0.1.47 used an incompatible snapshot format and
require manual backup/removal. Copy staging requires identical tree and content;
if only execute bits differ, it warns and publishes while tracking both actual
snapshots, avoiding perpetual refresh on DrvFS without metadata. Other permission
and timestamp changes are not tracked. Dry-run cannot check mode support.

Correctly targeted legacy symlinks migrate; per-entry legacy profile symlink
ownership is retired atomically before each copy change, including filtered runs,
copy prunes and previously migrated copies. A manifest write failure blocks the
corresponding copy action; if the subsequent copy fails, the conservative
retirement remains, so an old symlink is no longer eligible for profile-manifest
pruning and can be retried. Multiple actions are not transactional.

Source links resolving inside their bridge repo materialize as regular files or
directories; external, broken, cyclic links and special files block. Copy
destinations cannot overlap any configured bridge repo. Switch reconciles only
profiles referencing the old or new lane bridge; it blocks affected copy-mode
destinations, including those outside the default list. An explicit `--dest` with
profiles must cover all affected profile destinations. Prune counts include
managed copies. README documents operating and recovery limits.

## Evidence

- Both independent-review blockers were observed RED against `e9b9bf2` via
  `python3 -m unittest discover -s tests -p test_managed_copy.py -k drvfs_mode -v`
  and `-k profile_migrates -v`, then GREEN after corrections. Tests exercise the
  actual CLI with simulated mode loss, mode-only source change, no perpetual
  refresh, destination-mode edits, filtered migration, copy prune, and a manually
  recreated link. Added content-mismatch and injected manifest/copy-write
  failure checks, plus single-snapshot backward compatibility.
- Isolated `HOME=<scratch> PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 python3
  -m unittest discover -s tests -v`: 286 run, 285 passed, 1 skipped
  (`test_opencode_native_loader_discovers_projected_subagent`, requires
  `OPENCODE_TEST_BIN`). Log: `/home/ryushe/.hermes/cache/scratch/ais-final-home.jx9r8G/suite.log`.
- Both independent reproducers ran successfully: recreated symlink preserved;
  simulated DrvFS copies with warning and exit 0. The other reproducer also
  copied real read-only BBH `skills/bunny` to scratch, checked the four linked
  files materialized with equal bytes, and repeated the sync as a no-op.
- Source CLI and scratch-isolated installed CLI, module and package metadata all
  report `0.1.49`; install used `/usr/bin/python3 -m pip install --no-deps
  --no-build-isolation --target <scratch>/install <scratch>/src`.
- Windows detector source `aiskillsync/windows_mount.py` and its
  `tests/test_windows_mount.py` are included in this commit, not an external
  untracked dependency.

## Follow-up review and resume

Independent re-review of `54fb03a7424cb902bd43b95ac92c3954aa84e808` verified the two original failure reproducers and simulated both DrvFS mode directions. An earlier, unpublished candidate could leave a stale symlink claim after copy removal; do not upgrade from an experimental copy of that checkpoint without clearing its stale test state. A separate reviewer identified the observed WSL VirtioFS `drvfsC0` drive tag as missing. A focused test failed on the prior commit and passed with numbered drive-tag detection; unfamiliar tags remain ambiguous. See https://github.com/microsoft/WSL/issues/14301 for the observed tag. Independent review of implementation `a9506ea0109e3349a2babe6df418b23e7ba713e9` found no blocker in the correction, verified standard beta-to-candidate upgrade with recreated links preserved, and ran the full suite: 287 tests run, one skipped. This dossier checkpoint updates real-WSL evidence; the reviewed implementation remains unchanged. Real WSL and consumer discovery are verified as described below.

## Risks and activation boundary

On user-authorized Abommie WSL2 kernel 6.18.54.1, a disposable Windows-backed
9p DrvFS mount test exercised automatic detection, dry-run, first copy, Windows
PowerShell reads, materialization of a repo-internal linked reference, repeat
no-op, source additions/updates/removals, local edits made from Windows blocking
replacement, stale prune, and preservation of an unrelated directory. The
expected execute-bit mismatch warning appeared without blocking copies. Source
module SHA-256 values matched between the reviewed checkout and WSL fixture.
With separate user approval, a harmless probe was copied into the configured
Windows `.agents/skills` path using an isolated config; user screenshot showed
it discovered in the ChatGPT desktop skill picker. The screenshot establishes
discovery, not a completed skill execution. The owned probe was hash-verified,
removed, and Windows-native readback confirmed absence. No existing config was
modified. WSL with metadata enabled and non-9p mount variants remain untested.

Snapshots and `copytree` are not a lock or adversarial race sandbox: stop
concurrent source-link mutations and destination writers. A failed/interrupted
apply can leave a sibling backup or unowned tree; inspect before retrying. Lane
copy switching and owner/source/mode changes remain blocked. Pruning does not
cover old unselected destinations or unavailable/disabled source roots. Parent
should review full combined tree, especially TOCTOU and recovery semantics, and
decide integration and activation separately.
