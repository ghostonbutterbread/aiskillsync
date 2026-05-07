# aiskillsync

Global AI skill synchronization tooling.

This repo is intended to replace per-project `sync_skills.sh` scripts with one
config-driven sync layer that can bridge skills from multiple repos into AI
provider skill directories.

## Implemented CLI

Run the stdlib-only module entrypoint from this repo:

```bash
python3 -m aiskillsync --help
python3 -m aiskillsync init --dry-run
python3 -m aiskillsync config --default
python3 -m aiskillsync config
python3 -m aiskillsync list
python3 -m aiskillsync doctor
```

Use `--config` to point at a non-default config:

```bash
python3 -m aiskillsync --config ./config.yaml list
python3 -m aiskillsync --config ./config.yaml doctor
```

`init` writes `~/.config/aiskillsync/config.yaml` unless `--dry-run` is used.
It will not overwrite an existing config unless `--force` is passed.

`config` is strict and requires an existing config file. Use
`config --default` or `config --show-default` to preview the default template
without creating or loading a config.

The current implementation covers Phase 1 and Phase 2 from
`docs/AISKILLSYNC_SPEC.md`:

- config loading with `~` and environment variable path expansion
- bridge and skill discovery
- destination classification
- `list` and `doctor` reporting

Filesystem mutation for syncing is not implemented yet. The only write-capable
command is `init`, and it only writes the config file.

Reserved future commands are tracked in the spec but intentionally not exposed
yet: `sync`, `add`, and `remove`.

## Config

Default path:

```text
~/.config/aiskillsync/config.yaml
```

Example:

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

The MVP parser supports the simple YAML shape above and intentionally avoids
external Python packages.

## Discovery Commands

`list` shows each configured bridge, whether it is enabled, its local path,
repo URL, branch, discovered skill count, skill names, and a destination status
summary.

`doctor` validates that the config exists and parses, bridge names are unique,
enabled bridge local paths exist or have a repo URL, enabled bridge
`skills_path` directories exist when the local path already exists, skill
directories contain `SKILL.md`, enabled bridges do not export duplicate skill
names, and destination entries are classified as missing, already linked,
regular directory/copy, unexpected symlink, or path conflict. Disabled bridge
checks are reported as `SKIP` unless a global check, such as duplicate bridge
names, still affects the exit status.

## Verification

Targeted stdlib smoke tests:

```bash
python3 -m unittest discover -s tests
```

## Personal migration helper

One-time helper for migrating Ryushe's current copied/symlinked Bounty Harness
skills into clean symlinks:

```bash
python3 scripts/migrate_aiskillsync_personal.py --list-sources
python3 scripts/migrate_aiskillsync_personal.py --backup-differs --apply
```

By default it processes Codex and Claude skill paths only. Ghost/OpenClaw is
available explicitly with `--dest ghost`.
