# aiskillsync Implementation Spec

Status: draft
Owner: Ghost / Ryushe
Canonical path: `docs/AISKILLSYNC_SPEC.md`
Created: 2026-05-07

## Goal

Build a global AI skill synchronization tool that replaces per-repo `sync_skills.sh` scripts with one config-driven CLI.

The tool bridges skill directories from multiple source repositories into provider skill directories such as Codex and Claude, using symlinks by default.

## Core use case

Ryushe has multiple repos that may own skills independently:

- `bug_bounty_harness`
- `bounty-core`
- `bounty-tools`
- future skill repos

Each repo should remain the source of truth for its own skills. `aiskillsync` should discover, clone/pull, and link those skills into AI provider paths.

## Non-goals for MVP

- Do not centralize all skills into one giant skill source repo.
- Do not modify skill contents.
- Do not silently overwrite user-modified destination skills.
- Do not require OpenClaw-specific APIs.
- Do not depend on external Python packages for the MVP.

## Terminology

### Bridge

A bridge is a configured source repo that contains one or more skills.

Example:

```yaml
repo_dir: ~/.config/aiskillsync/repos

bridges:
  - name: bounty-harness
    repo: https://github.com/ghostonbutterbread/bug-bounty-harness.git
    path: ~/projects/bug_bounty_harness
    skills_path: skills
    branch: master
    enabled: true
```

### AI skill path

A destination directory where a provider reads skills.

Example:

```yaml
ai_skill_paths:
  codex: ~/.codex/skills
  claude: ~/.claude/skills
  ghost: ~/.openclaw/workspace/skills
```

## Config

Default config path:

```text
~/.config/aiskillsync/config.yaml
```

MVP config:

```yaml
bridges:
  - name: bounty-harness
    repo: https://github.com/ghostonbutterbread/bug-bounty-harness.git
    path: ~/projects/bug_bounty_harness
    skills_path: skills
    branch: master
    enabled: true

ai_skill_paths:
  codex: ~/.codex/skills
  claude: ~/.claude/skills
  ghost: ~/.openclaw/workspace/skills

sync:
  mode: symlink
  pull_before_sync: true
  clone_if_missing: true
  default_destinations:
    - codex
    - claude
```

## CLI

Implemented Phase 1/2/3 command shape:

```bash
aiskillsync init
aiskillsync config
aiskillsync config --default
aiskillsync list
aiskillsync doctor
aiskillsync sync main
aiskillsync sync codex --repo bounty-harness
aiskillsync sync openclaw --repo https://github.com/ghostonbutterbread/bug-bounty-harness.git
aiskillsync sync all
aiskillsync sync bounty-harness --dest codex --dest claude
aiskillsync sync 1 2
aiskillsync sync all --dry-run
aiskillsync add https://github.com/org/ai-skills.git
aiskillsync add https://github.com/org/ai-skills.git ~/projects/ai-skills
aiskillsync remove ai-skills
```

### `init`

Creates `~/.config/aiskillsync/config.yaml` if missing.

Behavior:

- Prompt for provider destinations or use defaults.
- Prompt for first bridge, or create an empty config.
- Do not overwrite an existing config unless `--force` is passed.

### First-run default config

When `--config` is omitted and `~/.config/aiskillsync/config.yaml` is missing,
normal config-loading commands create the parent directories, write the default
template, and then load it. This applies to `config`, `list`, `doctor`, and
`sync`.

Explicit `--config` paths stay strict and must already exist.

`config --default` / `config --show-default` only prints the template and does
not create or load a config file.

### `list`

Shows configured repos and discovered skills.

Must show:

- repo number
- name
- enabled/disabled
- local path
- repo URL
- branch
- skills count
- destination status summary

### `doctor`

Validates config and filesystem state.

Checks:

- config exists and parses
- repo names are unique
- destination names are unique
- enabled repo local paths exist or are cloneable
- enabled repo `skills_path` exists when the repo local path already exists
- enabled repos have `skills_path`
- each skill dir has `SKILL.md`
- duplicate skill names across enabled repos are reported as conflicts
- disabled repo-local checks are labeled `SKIP` unless a global check affects
  the command exit status
- destination entries are classified:
  - already linked to correct source
  - missing
  - regular directory/copy
  - symlink to unexpected target
  - non-directory path conflict

### `sync`

Synchronizes selected repos into selected destinations.

Examples:

```bash
aiskillsync sync main
aiskillsync sync codex
aiskillsync sync claude --repo bounty-harness
aiskillsync sync ghost --repo bounty-harness
aiskillsync sync openclaw --repo bounty-harness
aiskillsync sync codex --repo https://github.com/ghostonbutterbread/bug-bounty-harness.git
aiskillsync sync all
aiskillsync sync bounty-harness
aiskillsync sync 1 2
aiskillsync sync bounty-harness --dest codex --dest claude
aiskillsync sync all --dry-run
```

Future Phase 4 adoption command shape:

```bash
aiskillsync sync all --adopt
```

Default behavior:

- apply unless `--dry-run` is passed. `--apply` remains accepted as a
  backwards-compatible explicit no-op.
- Destination-first syntax is preferred:
  - `main` selects `codex` and `claude`.
  - `codex` selects only the Codex destination.
  - `claude` selects only the Claude destination.
  - `ghost` and `openclaw` are aliases for the Ghost/OpenClaw destination.
  - `all` selects all configured destinations when paired with `--repo`.
- Repo selection defaults to all configured repos.
- `--repo` is repeatable and selects configured repos by name or repo URL.
- If a `--repo` URL matches a configured repo URL, the configured repo path,
  skills path, and branch are used; no duplicate ad-hoc clone is created.
- If a `--repo` URL is not configured, aiskillsync creates an ad-hoc in-memory
  repo for that run only. The clone path is deterministic:
  `repo_dir/<repo-slug>-<url-hash>` (default: `~/.config/aiskillsync/repos/<repo-slug>-<url-hash>`).
  Existing paths are never deleted or replaced; normal repo path validation
  and git safety checks apply.
- Legacy repo-first syntax remains supported. `sync all` with no `--repo`
  still means all repos into `sync.default_destinations`, and `--dest` remains
  a repeatable legacy destination filter.
- Dry-run reports planned clone and pull work but does not run git or mutate the
  filesystem.
- Default sync clones a missing selected enabled repo root when `repo` exists
  and `sync.clone_if_missing` is true. If `branch` is configured, clone uses
  `git clone --branch <branch> <repo> <path>`; otherwise it uses
  `git clone <repo> <path>`.
- Default sync updates an existing selected enabled repo root when
  `sync.pull_before_sync` is true, but only if the path is a git repo. The
  update command is `git -C <path> pull --ff-only`; a non-zero exit blocks sync.
- Disabled repos are not cloned or pulled.
- Link source skill dirs into destination skill dirs.
- Sync output ends with a summary of repo action counts
  (`planned`/`cloned`/`pulled`/`errors`), destination action counts
  (`linked`/`skipped`/`conflicts`/`errors`/`noops`), and final status
  (`applied`/`dry-run`/`blocked`).
- TTY output colorizes successful `LINK`/`CLONE`/`PULL` and applied status
  green, `SKIP`/`PLAN` and dry-run status yellow/blue, and
  `ERROR`/`CONFLICT` and blocked status red. Non-TTY output remains plain
  unless `FORCE_COLOR` is set; `NO_COLOR` disables automatic color.

Safety rules:

- Never delete or overwrite unrelated skill names.
- Never overwrite a non-symlink destination unless `--adopt` or `--backup-differs` is explicitly used.
- If destination is already a correct symlink, skip.
- If destination is a symlink to another source, report conflict unless adoption is explicit.
- If destination is a copied dir with matching source `SKILL.md`, it may be replaced with symlink during adoption.
- If destination differs, back it up before replacement when adoption mode is enabled.

Phase 3 apply supports only the first two safety cases:

- `already-linked`: skip.
- `missing`: create the destination symlink.
- `directory-copy`, `unexpected-symlink`, and `path-conflict`: report a
  conflict and block the whole apply.

Phase 3 does not delete, adopt, back up, replace, or otherwise modify existing
destination entries.

## Migration helper

Current one-off helper:

```text
scripts/migrate_aiskillsync_personal.py
```

Purpose:

- Migrate Ryushe's current copied/symlinked Bounty Harness skills into clean symlinks.
- Default destinations: Codex + Claude only.
- Ghost/OpenClaw only when explicitly requested.

This helper is temporary and should eventually be replaced by first-class `aiskillsync sync --adopt` behavior.

## Implementation plan

### Phase 1 — Repo skeleton and config

Deliverables:

- Python package or standalone CLI entrypoint.
- `aiskillsync init`
- `aiskillsync config`
- config read/write helpers
- path expansion helper
- no external dependencies if possible

Acceptance:

```bash
python3 -m aiskillsync --help
python3 -m aiskillsync init --dry-run
python3 -m aiskillsync config --default
```

Plain `python3 -m aiskillsync config` creates the default config on first run
when `--config` is omitted. Explicit `--config` paths remain strict.

### Phase 2 — Discovery and doctor

Deliverables:

- Bridge discovery
- Skill discovery
- Destination classification
- Duplicate skill conflict detection
- `aiskillsync list`
- `aiskillsync doctor`

Acceptance:

- Finds Bounty Harness skills.
- Reports `appmap` and `brainstorm-spec` as absent from current provider paths when applicable.
- Reports same-name destination copies without modifying them.

### Phase 3 — Sync engine

Deliverables:

- `aiskillsync sync <bridge|all|numbers>`
- `--dest` filtering
- symlink mode
- dry-run report
- apply mode
- pull-before-sync
- clone-if-missing

Acceptance:

- Can sync Bounty Harness skills into Codex and Claude with symlinks.
- Does not touch Ghost unless `--dest ghost` is passed or configured as default.
- Does not touch unrelated destination skills.

Implemented Phase 3 scope:

- `sync` selects bridges by name, `all`, or 1-based list indexes.
- `--dest` is repeatable; omitted destinations come from
  `sync.default_destinations`.
- Destination-first groups `main`, `codex`, `claude`, `ghost`/`openclaw`, and
  `all` are implemented.
- `--repo` is repeatable and accepts configured repo names, configured repo
  URLs, or unconfigured ad-hoc repo URLs.
- `sync.mode` must be `symlink`.
- Default sync applies changes.
- `--dry-run` previews without git or destination mutations.
- `--apply` is accepted for compatibility and creates only missing destination symlinks and skips already-correct
  symlinks.
- Destination conflicts block apply before any symlink is created.
- Clone-if-missing and pull-before-sync run for default `sync`; dry-run is
  report-only, and `list`, `doctor`, and `config` do not run git.

### Phase 4 — Adoption / migration mode

Deliverables:

- `--adopt` or `--backup-differs`
- stable backup root per run
- health report
- migrated personal helper logic folded into sync engine

Acceptance:

- Existing copied Bounty Harness skills are backed up and replaced by symlinks.
- Conflicting or unrelated entries remain untouched.
- Health report lists removed/backed-up/linked/skipped/conflicts.

### Phase 5 — Compatibility shims

Deliverables:

- Replace `bug_bounty_harness/sync_skills.sh` with a compatibility shim.
- Keep `setup.sh --sync` working.
- Optional shims for future repos.

Acceptance:

```bash
cd ~/projects/bug_bounty_harness
./setup.sh --sync
```

still works and delegates to `aiskillsync`.

### Phase 6 — Repo config management

Implemented repo-first `add` and `remove` commands.

Deliverables:

- `aiskillsync add <repo-url-or-path> [local-path] --skills-path skills --branch main`
- `aiskillsync repo add <repo-url-or-path> [local-path]`
- `aiskillsync remove <repo-name-or-url>`
- `aiskillsync repo remove <repo-name-or-url>`
- targeted config edits that preserve unrelated comments/keys where practical
- atomic config writes

Acceptance:

- Can add a disabled or enabled repo entry without corrupting existing config.
- Can remove a repo entry by name or URL without touching local repos or destination
  skill directories.
- URL repos without explicit local path use `repo_dir/<repo-name>`; ad-hoc sync URLs use `repo_dir/<repo-slug>-<url-hash>`.

## Open design decisions

1. Should `sync` be dry-run by default forever, or only during beta?
   - Resolved: `sync` applies by default; `--dry-run` is the preview mode.

2. Should default destinations include Ghost/OpenClaw?
   - Recommendation: no for migration; yes only if configured explicitly later.

3. Should config use YAML or TOML?
   - Recommendation: YAML is friendlier for this shape, but TOML avoids PyYAML dependency if using `tomllib` only for read. If no external deps is strict, use TOML.

4. Should duplicate skill names support priority ordering?
   - Recommendation: report conflicts first. Add explicit `priority` later only if needed.

## Suggested first Codex implementation prompt

```text
Implement Phase 1 and Phase 2 from docs/AISKILLSYNC_SPEC.md.
Keep dependencies stdlib-only. Create a Python module/CLI for aiskillsync with config loading, path expansion, bridge/skill discovery, list, and doctor commands. Do not implement filesystem mutation yet except optional config init writes. Add targeted tests or smoke checks. Preserve scripts/migrate_aiskillsync_personal.py unchanged except imports if needed.
```
