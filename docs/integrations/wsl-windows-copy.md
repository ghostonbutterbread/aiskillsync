# WSL / Windows managed copies

## Intent and integration

Windows-native consumers cannot use WSL-only symlink targets. This feature adds
per-destination copy projection on detected WSL Windows mounts or explicit
`sync.copy_destinations`; other skill destinations retain symlinks. Worktree:
`/home/ryushe/worktrees/aiskillsync-wsl-windows-copy`, branch
`feat/wsl-windows-copy`; base `04a78ec9564b4081d09b597fa0331da506115d97`,
integration target `beta`. Parent owns combined review and integration; no
push, merge or activation. Initial RED checkpoint
`5e684b4963ccd22d5ddfb41c9742cc2ada8cb992`; original implementation checkpoint
`aacdc9ce0ccb7d855bf1c86db0accdb84ab97d38`. The reviewer-fix commit is the
branch tip supplied separately in the agent handoff (a commit cannot embed its
own SHA). The next action is to combine the independently owned mount module,
review the exact combined tree, rerun tests, and retain this dossier until
integration.

## Contract and reviewer corrections

Per-copy records alongside the config bind source, destination, profile and
snapshots. Unchanged owned copies refresh or prune, locally edited and unowned
copies block, and correctly targeted legacy symlinks migrate. Source links
resolving inside their bridge repo materialize as regular files/directories;
external, broken, cyclic links and special files block. Source snapshot includes
linked target contents; copy destinations cannot overlap any configured bridge
repo. File executable bits participate in snapshots. A destination that cannot
represent them is rejected during staging with an explicit diagnostic; dry-run
cannot detect this, and other permissions and timestamps are not tracked.
Unreleased 0.1.47 records have the previous snapshot format and require manual
backup/removal before use. README describes operating and recovery limits.

Switch reconciles only profiles referencing the old or new lane bridge; it blocks
if any affected destination is copy-mode, including destinations outside the
default list. An explicit `--dest` with profiles must cover all affected profile
destinations, since lane state is global. Unrelated profiles remain untouched.
Profile-manifest prunes and switch stale-link cleanup do not double-unlink.
Summary prune counts include managed copies.

## Evidence and dependency boundary

- RED to GREEN observed individually for nested link materialization, mode-only
  refresh, unsupported destination mode diagnostic, unrelated copy profile,
  copy prune count, source-repo overlap, and double-pruned profile lane links.
- `PYTHONPATH="$PWD" python3 -m unittest discover -s tests -p test_managed_copy.py -v`:
  16 passed (disposable real CLI and source/destination fixtures). Mixed profile
  test subsequently strengthened to use an existing copy plus stale Linux link;
  focused lane-switch tests passed.
- With the sibling's **untracked** module and tests present, isolated
  `HOME=<scratch> PYTHONPATH="$PWD" python3 -m unittest discover -s tests -v`:
  284 run: 283 passed, 1 skipped (`test_opencode_native_loader_discovers_projected_subagent`,
  requires `OPENCODE_TEST_BIN`). The module-specific Windows mount suite: 25 passed.
- Actual read-only BBH source `/home/ryushe/projects/bug_bounty_harness`, real
  `skills/bunny`: disposable CLI copy and repeat no-op passed; all four agent
  symlink targets materialized as equal regular files. No source or user config
  writes. The temporary HOME on the final suite also isolates its backup files.
- `python3 -m aiskillsync --version` and a scratch `pip --target` installation's
  metadata, runtime version and module CLI each report `0.1.48`. First attempt
  with the active Python failed because it had no pip; `/usr/bin/python3 -m pip`
  succeeded with `--no-deps --no-build-isolation`.
- **Required dependency not included in this commit:**
  `aiskillsync/windows_mount.py` and `tests/test_windows_mount.py` are owned by
  the concurrent sibling and intentionally unstaged. Parent must collect and
  commit them before reviewing or using the combined feature; this branch alone
  fails imports. Rerun the full suite against the exact combined tree.

## Risks, blocker, and activation boundary

Real WSL/Windows-backed mount behavior, Windows-side reading, chmod/mode
semantics, refresh, prune, and local-edit protection remain unverified: this host
is Linux (kernel `7.0.0-38-generic`), with no WSL environment. Before activation,
run against a disposable WSL Windows-backed mount, with and without DrvFS
metadata as available: preview and apply, verify Windows-side reads, add/change/
delete source, execute a mode-only change, repeat for no-op or explicit
unsupported-mode error, and test local-edit conflict and prune. Current behavior
may reject normal DrvFS mounts without metadata rather than silently losing
executable bits. Do not claim WSL compatibility from the Linux suite.

Snapshots and `copytree` are not a lock or adversarial race sandbox: stop
concurrent source-link mutations and destination writers. Multiple actions are
not transactional; interrupted/failed applies may leave a sibling backup or an
unowned tree. Lane copy switching and owner/source/mode changes remain blocked.
Pruning does not cover old unselected destinations or unavailable/disabled
source roots. Parent should obtain independent review of the complete combined
tree, especially link validation/TOCTOU and recovery semantics, and decide
integration and activation separately; remove this branch-local dossier during
authorized integration.
