# Agent-file synchronization integration dossier

- **Status:** implemented; awaiting parent independent review (not released)
- **Owner:** delegated implementation for parent task `t_cf47e3cc`
- **Branch / owning ref:** `feat/agent-file-sync`
- **Worktree:** `/home/ryushe/worktrees/aiskillsync-agent-sync`
- **Base / immutable starting checkpoint:** `ef807e19b52ba9b9bbfe4cd64ab5b0bd238afd22`
- **Intended integration target:** `beta`
- **Last updated:** 2026-10-26
- **Implementation checkpoint:** the scoped implementation commit containing this
  dossier on `feat/agent-file-sync`; resolve with `git rev-parse feat/agent-file-sync`.
  The exact resulting SHA is also returned in the parent handoff. No merge/push
  authority is delegated here.
- **Inspiration / canonical reference:** explicit Ryu request to synchronize BBH's
  physical `agents/` definitions into configured runtimes, primarily Claude's
  `~/.claude/agents`, without representing agent files as skills.

## Intent and boundaries

Add opt-in agent-file projection without altering existing skill defaults or
editing real user configuration. Canonical BBH file migration, installation on
Hoster, integration/release, and runtime activation belong to the parent.
No provider translation or assumed universal Markdown support is introduced.

## Implemented contract

- Optional `bridges[].agents_path` selects a directory relative to the checkout
  (absolute/expanded paths follow the existing skills-path convention).
- `ai_agent_paths` explicitly maps destination names to agent directories. It has
  no defaults; roots may not alias another agent or skill root. Config parsing,
  serialization, and `config` output preserve/display the new fields.
- New separate command:
  `aiskillsync agents --repo <configured-selector> --dest <configured-name>`;
  repeatable `--repo`, `--dest`, and `--agent <frontmatter-name>`; `--dry-run` and
  explicit `--adopt`. No `--overwrite`, ad-hoc repository import, or skill sync.
- Active profiles require `agents --profile <name>` instead of repo/destination
  overrides. Profile source bridges and destination ownership remain authoritative.
  Skill category/name filters do not select agents. Agent-only destinations are
  allowed in profiles; existing skill sync ignores them.
- Active-lane enablement, configured branch checks, Git auth preflight, and
  clone/fast-forward-pull helpers are reused. Agent materialization suppresses
  unrelated skill-change reporting. Inactive/unconfigured sources are not pulled.
- Only immediate `*.md` children with delimited frontmatter, a safe lowercase
  name, and a nonempty description are discovered. Scalar required fields plus
  literal/folded description blocks are supported; other provider fields and
  body bytes are left alone. Invalid/non-agent Markdown is reported as skipped,
  not installed. Missing directories, no valid definitions, duplicate selected
  names, and missing requested names block destination apply.
- `<name>.md` links directly to the source file. Correct links are no-ops.
  Conflicts block all destination writes unless adopted; backups go under
  `<migration-backup-root>/agents/<destination>/<name>.md`, preserving symlinks
  rather than touching their targets. Existing backups are refused.
- Pure plan APIs live in the owning `aiskillsync/agents.py` module:
  `discover_agents`, `resolve_agent_sync_request`, `build_agent_sync_plan`, and
  `apply_agent_sync_plan`, with dedicated `AgentDefinition`, `AgentAction`, and
  `AgentSyncPlan` types. Invalid requests raise `ConfigError`; plan/filesystem
  blockers are represented in plans or raised as `ValueError`/`OSError` on apply.
  Planning never materializes repositories. A dry-run plan cannot mutate files
  even if passed to apply.
- No agent pruning/manifests. Filtered-out, stale, and unmanaged entries remain
  untouched. `switch` is intentionally still skill/launcher-only; a subsequent
  explicit agent preview/adoption repoints selected old-lane links.

## Evidence and review

- Baseline: `python3 -m unittest discover -s tests` passed **115 tests** before edits.
- RED/GREEN tracer bullets verified the absent CLI, duplicate-name rejection,
  symlink adoption, profile isolation/agent-only destinations, clone/pull,
  focused adoption, apply-time state checks, root-alias protection, block
  metadata parsing, and non-directory root handling before implementation/fixes.
- Focused: `python3 -m unittest discover -s tests -p test_agents.py` passed
  **20 tests**. Coverage includes missing/invalid definitions, same-bridge and
  cross-bridge duplicates, real local Git clone/pull, wrong/detached/non-Git
  branch pins, active-lane isolation, dry-run snapshots, repeated no-op,
  profile ownership, skill/agent separation, symlink/dangling-link backups,
  backup collision refusal, unmanaged/stale preservation, and config roundtrips.
- Full suite: `python3 -m unittest discover -s tests` passed **135 tests**.
  Existing lane rollback tests deliberately emit failure diagnostics (also
  present in baseline) while passing their assertions.
- `git diff --check` passed. Source CLI reports `aiskillsync 0.1.23`; both
  `pyproject.toml` and `aiskillsync/__init__.py` bump together from 0.1.22.
- Built with `uv build --wheel --out-dir
  /home/ryushe/.hermes/cache/scratch/aiskillsync-agent-sync-wheel`.
  Inspected wheel metadata: `aiskillsync`, version `0.1.23`, no runtime
  dependencies, and `aiskillsync/agents.py` packaged.
- Installed that wheel into a disposable scratch venv. The actual installed
  console command passed preview/apply/no-op against a temporary profile fixture:
  `Created 1 agent symlink(s)` then `No agent symlinks needed`; preserved a local
  unmanaged agent and ignored `index.md`/code; no skill root, config, or skill
  projection manifest was created. Scratch receipt script:
  `/home/ryushe/.hermes/cache/scratch/verify_aiskillsync_agent_wheel.py`.
- Last upstream fetch: `origin/beta` remained at the supplied base checkpoint.
- Independent review: **pending parent**, not claimed by the implementer.

## Residuals and deferred activation

- **Missing consumer evidence:** real Claude discovery/load on Hoster after BBH
  migration and installation. Parent should configure `agents_path: agents` and
  only the intended Claude destination, preview `agents --profile <name>
  --dry-run`, apply the reviewed projection, then verify discovery in a fresh
  Claude process. This is an activation gate, not a claim made by fixture tests.
- **Deliberate non-feature:** agent deletion/rename cleanup is manual; filtered
  runs never prune. Lane switching does not implicitly mutate agent links.
- **Filesystem limitation:** apply rechecks all planned destination states and
  source branches before writes, refuses existing backups, and attempts to restore
  an adopted entry if link creation fails. A multi-entry I/O failure is not a
  transactional rollback of earlier successes. Symlink support is required.
- Enabled Git pulls refresh the entire selected checkout, not only agent files;
  existing skill links into that source can observe source updates. For a
  destination-isolated rollout of reviewed local files, disable
  `sync.pull_before_sync`. Agent projection itself never writes skill paths.
- Frontmatter support is intentionally a bounded required-field reader, not a
  full YAML parser or a provider schema validator. Only format-compatible
  runtimes should be configured; Codex/Hermes compatibility is not asserted.

## Handoff and decision gates

- **Resume:** independently review this feature diff against the exact base,
  rerun the full unittest suite and wheel fixture; parent then owns integration
  into `beta`, release/deployment, BBH migration, and activation checks.
- **Working-tree handoff:** implementation and dossier committed together; no
  user configuration, other worktree, integration branch, or remote was changed.
- **Integration gate:** parent independent review, passing tests, scoped clean
  diff, current upstream reconciliation. Remove this temporary dossier from the
  integration target when accepted, retaining its feature/history record.
- **Activation gate:** reviewed canonical BBH files plus explicit destination
  config and fresh Claude consumer evidence on Hoster.
- **Promotion gate:** separate parent/user release authority; no stable promotion
  or push is included here.

## Decision record

- 2026-10-26 — implement additive separate file projection; preserve skill
  defaults, use existing profile/lane source policy, defer release and runtime
  activation to parent; no automatic agent pruning or provider conversions.
