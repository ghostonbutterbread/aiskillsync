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
  codex: ~/.agents/skills
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
  codex: ~/.agents/skills
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

Implemented Phase 1/2 command shape:

```bash
aiskillsync init
aiskillsync config
aiskillsync config --default
aiskillsync list
aiskillsync doctor
```

Reserved future command shape:

```bash
aiskillsync sync all
aiskillsync sync bounty-harness
aiskillsync sync 1 2
aiskillsync add <name> <repo> --path <local-path> --skills-path skills --branch main
aiskillsync remove <name>
```

### `init`

Creates `~/.config/aiskillsync/config.yaml` if missing.

Behavior:

- Prompt for provider destinations or use defaults.
- Prompt for first bridge, or create an empty config.
- Do not overwrite an existing config unless `--force` is passed.

### `list`

Shows configured bridges and discovered skills.

Must show:

- bridge number
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
- bridge names are unique
- destination names are unique
- enabled bridge local paths exist or are cloneable
- enabled bridge `skills_path` exists when the bridge local path already exists
- enabled bridges have `skills_path`
- each skill dir has `SKILL.md`
- duplicate skill names across enabled bridges are reported as conflicts
- disabled bridge-local checks are labeled `SKIP` unless a global check affects
  the command exit status
- destination entries are classified:
  - already linked to correct source
  - missing
  - regular directory/copy
  - symlink to unexpected target
  - non-directory path conflict

### `sync`

Future placeholder. Not implemented in Phase 1/2.

Synchronizes selected bridges into selected destinations.

Examples:

```bash
aiskillsync sync all
aiskillsync sync bounty-harness
aiskillsync sync 1 2
aiskillsync sync bounty-harness --dest codex --dest claude
aiskillsync sync all --dry-run
aiskillsync sync all --adopt
```

Default behavior:

- dry-run unless `--apply`? Decision needed.
- Recommended for safety: dry-run by default for early versions, later switch to apply after confidence.
- Pull before sync if `sync.pull_before_sync` is true.
- Clone if missing when `sync.clone_if_missing` is true.
- Link source skill dirs into destination skill dirs.

Safety rules:

- Never delete or overwrite unrelated skill names.
- Never overwrite a non-symlink destination unless `--adopt` or `--backup-differs` is explicitly used.
- If destination is already a correct symlink, skip.
- If destination is a symlink to another source, report conflict unless adoption is explicit.
- If destination is a copied dir with matching source `SKILL.md`, it may be replaced with symlink during adoption.
- If destination differs, back it up before replacement when adoption mode is enabled.

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

Plain `python3 -m aiskillsync config` remains strict and requires an existing
config file.

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

### Phase 6 — Bridge config management

Future placeholder for `add` and `remove`.

Deliverables:

- `aiskillsync add <name> <repo> --path <local-path> --skills-path skills --branch main`
- `aiskillsync remove <name>`
- no-overwrite config edits with clear diffs or dry-run output

Acceptance:

- Can add a disabled or enabled bridge entry without corrupting existing config.
- Can remove a bridge entry by name without touching local repos or destination
  skill directories.

## Open design decisions

1. Should `sync` be dry-run by default forever, or only during beta?
   - Recommendation: dry-run by default until Ryushe is comfortable, then require `--dry-run` for preview.

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
