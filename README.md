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
python3 -m aiskillsync sync main
python3 -m aiskillsync sync codex --repo bounty-harness
python3 -m aiskillsync sync openclaw --repo https://github.com/ghostonbutterbread/bug-bounty-harness.git
python3 -m aiskillsync sync all
python3 -m aiskillsync sync bounty-harness --dest codex --dest claude
python3 -m aiskillsync sync 1 2 --apply
```

Use `--config` to point at a non-default config:

```bash
python3 -m aiskillsync --config ./config.yaml list
python3 -m aiskillsync --config ./config.yaml doctor
python3 -m aiskillsync --config ./config.yaml sync all --dry-run
```

On first run, normal commands that use the default config path create
`~/.config/aiskillsync/config.yaml` from the built-in template before loading
it. Explicit `--config` paths remain strict and must already exist.

`init` writes `~/.config/aiskillsync/config.yaml` unless `--dry-run` is used.
It will not overwrite an existing config unless `--force` is passed.

Use `config --default` or `config --show-default` to preview the default
template without creating or loading a config.

The current implementation covers Phase 1, Phase 2, and Phase 3 from
`docs/AISKILLSYNC_SPEC.md`:

- config loading with `~` and environment variable path expansion
- bridge and skill discovery
- destination classification
- `list` and `doctor` reporting
- safe dry-run sync planning
- sync-only bridge clone/pull materialization
- optional `sync --apply` creation of missing symlinks only

`sync` is dry-run by default. `--apply` is intentionally narrow in Phase 3: it
may first clone missing selected enabled bridge roots when `bridge.repo` exists
and `sync.clone_if_missing` is true, or pull existing selected enabled git
bridge roots when `sync.pull_before_sync` is true. It then creates destination
symlinks that are missing. Existing correct symlinks are skipped, and existing
directories, files, or symlinks to unexpected targets are conflicts that block
the whole apply. It does not delete, adopt, back up, or replace existing
entries; those behaviors are reserved for Phase 4.

Reserved future commands are tracked in the spec but intentionally not exposed
yet: `add` and `remove`.

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

When `--config` is omitted and the default config file is missing, `config`,
`list`, `doctor`, and `sync` create the parent directories and write this
template before loading it. `config --default` only prints the template.

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

## Sync Command

`sync` now prefers destination-first arguments. Destination groups are:

- `main`: Codex and Claude
- `codex`: Codex only
- `claude`: Claude only
- `ghost` or `openclaw`: Ghost/OpenClaw only
- `all`: every configured destination when used with `--repo`

Repo selection is optional and defaults to all configured bridges. Use
repeatable `--repo` flags to select configured bridge names or repo URLs:

```bash
python3 -m aiskillsync sync main
python3 -m aiskillsync sync codex
python3 -m aiskillsync sync openclaw --repo bounty-harness
python3 -m aiskillsync sync main --repo bounty-harness --repo bounty-tools
python3 -m aiskillsync sync codex --repo https://github.com/ghostonbutterbread/bug-bounty-harness.git
```

If a `--repo` URL matches an existing configured bridge URL, aiskillsync uses
that bridge's configured local path and does not create a duplicate clone. An
unconfigured repo URL is treated as an ad-hoc bridge for this run only. Its
deterministic clone path is
`${XDG_CACHE_HOME:-~/.cache}/aiskillsync/repos/<repo-slug>-<url-hash>`. If that
path already exists, normal sync safety checks apply; aiskillsync does not
delete or replace it.

The old bridge-first syntax remains supported. `sync all` still means all
bridges into `sync.default_destinations`, and repeated `--dest` flags still
select legacy destinations:

```bash
python3 -m aiskillsync sync all
python3 -m aiskillsync sync bounty-harness
python3 -m aiskillsync sync 1 2
python3 -m aiskillsync sync bounty-harness --dest codex --dest claude
```

Ghost/OpenClaw is not touched unless it is explicitly selected with
`sync ghost`, `sync openclaw`, `--dest ghost`, `--dest openclaw`, `sync all
--repo ...`, or included in `sync.default_destinations`.

Only `sync.mode: symlink` is supported. Dry-run never runs git and never
mutates bridge or destination paths; it reports planned `PLAN` clone and pull
steps. `--apply` may run `git clone`, including `--branch <branch>` when a
bridge branch is configured, for missing selected enabled bridge roots with a
repo URL and `sync.clone_if_missing: true`. For existing selected enabled bridge
roots with `sync.pull_before_sync: true`, `--apply` requires the path to be a
git repo and runs `git -C <path> pull --ff-only`; any non-zero pull blocks the
sync before symlink creation. Disabled bridges are never cloned or pulled.

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
