# aiskillsync

Global AI skill synchronization tooling.

This repo is intended to replace per-project `sync_skills.sh` scripts with one
config-driven sync layer that can bridge skills from multiple repos into AI
provider skill directories.

## Installation

Install as an isolated CLI with pipx:

```bash
pipx install .
aiskillsync --help
aiskillsync init --dry-run
```

Or install into the active Python environment with pip:

```bash
python3 -m pip install .
aiskillsync --help
```

The source-tree module entrypoint still works without installation:

```bash
python3 -m aiskillsync --help
```

Packaged installs expose only the `aiskillsync` console script.

## Implemented CLI

Run the installed console script:

```bash
aiskillsync --help
aiskillsync init --dry-run
aiskillsync config --default
aiskillsync config
aiskillsync list
aiskillsync doctor
aiskillsync add https://github.com/example/ai-skills.git
aiskillsync add https://github.com/example/ai-skills.git ~/projects/ai-skills
aiskillsync remove ai-skills
aiskillsync sync main
aiskillsync sync codex --repo bounty-harness
aiskillsync sync openclaw --repo https://github.com/ghostonbutterbread/bug-bounty-harness.git
aiskillsync sync all
aiskillsync sync bounty-harness --dest codex --dest claude
aiskillsync sync 1 2
aiskillsync sync all --dry-run
aiskillsync sync codex --repo bounty-harness --skill xss --adopt --dry-run
```

You can replace `aiskillsync` with `python3 -m aiskillsync` when running from a
checkout or an environment where the console script is not on `PATH`.

Use `--config` to point at a non-default config:

```bash
aiskillsync --config ./config.yaml list
aiskillsync --config ./config.yaml doctor
aiskillsync --config ./config.yaml sync all --dry-run
```

On first run, normal commands that use the default config path create
`~/.config/aiskillsync/config.yaml` from the built-in template before loading
it. Explicit `--config` paths remain strict and must already exist.

`init` writes `~/.config/aiskillsync/config.yaml` unless `--dry-run` is used.
It will not overwrite an existing config unless `--force` is passed.

Use `config --default` or `config --show-default` to preview the default
template without creating or loading a config.

The current implementation covers Phase 1, Phase 2, Phase 3, and the first
explicit-adoption slice of Phase 4 from
`docs/AISKILLSYNC_SPEC.md`:

- config loading with `~` and environment variable path expansion
- repo and skill discovery
- destination classification
- repo add/remove config management
- `list` and `doctor` reporting
- safe dry-run sync preview
- sync-only repo clone/pull materialization
- default `sync` creation of missing symlinks only
- opt-in `sync --adopt` migration with backups, skill filters, and denylists

`sync` applies by default. The compatibility `--apply` flag is accepted but no
longer required. Apply is intentionally narrow in Phase 3: it may first clone
missing selected enabled repo roots when `repo` exists
and `sync.clone_if_missing` is true, or pull existing selected enabled git
repo roots when `sync.pull_before_sync` is true. It then creates destination
symlinks that are missing. Existing correct symlinks are skipped, and existing
directories, files, or symlinks to unexpected targets are conflicts that block
the whole apply. It does not delete, adopt, back up, or replace existing
entries unless `--adopt` is explicitly passed.

`--adopt` is migration mode, not normal sync. It backs up same-name destination
entries under `~/.cache/aiskillsync-migration/<timestamp>/...` and replaces
them with symlinks to selected repo skills. Prefer pairing it with
`--skill <name>` or a denylist so migrations stay focused.

`add` and `remove` are exposed as repo-first convenience commands and mirror
`repo add` / `repo remove`.

## Config

Default path:

```text
~/.config/aiskillsync/config.yaml
```

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
  migration_denylist:
    - local-private-skill
```

The MVP parser supports the simple YAML shape above and intentionally avoids
external Python packages.

When `--config` is omitted and the default config file is missing, `config`,
`list`, `doctor`, and `sync` create the parent directories and write this
template before loading it. `config --default` only prints the template.

## Discovery Commands

`list` shows each configured repo, whether it is enabled, its local path,
repo URL, branch, discovered skill count, skill names, and a destination status
summary.

`doctor` validates that the config exists and parses, repo names are unique,
enabled repo local paths exist or have a repo URL, enabled repo
`skills_path` directories exist when the local path already exists, skill
directories contain `SKILL.md`, enabled repos do not export duplicate skill
names, and destination entries are classified as missing, already linked,
regular directory/copy, unexpected symlink, or path conflict. Disabled repo
checks are reported as `SKIP` unless a global check, such as duplicate repo
names, still affects the exit status.

## Repo Management

Add a repo URL to the config. If no local location is given, aiskillsync uses
`repo_dir/<repo-name>`; by default `repo_dir` is
`~/.config/aiskillsync/repos`. This updates config only; the repo is cloned on
the next `sync` when selected, unless `--dry-run` is used.

```bash
aiskillsync add https://github.com/org/ai-skills.git
aiskillsync add https://github.com/org/ai-skills.git ~/projects/ai-skills
aiskillsync repo add https://github.com/org/ai-skills.git --name ai-skills --branch main
aiskillsync add ~/projects/local-skill-repo
```

Remove a repo from config without deleting its local checkout or any provider
skill symlinks:

```bash
aiskillsync remove ai-skills
aiskillsync repo remove https://github.com/org/ai-skills.git
```

## Sync Command

`sync` now prefers destination-first arguments. Destination groups are:

- `main`: Codex and Claude
- `codex`: Codex only
- `claude`: Claude only
- `ghost` or `openclaw`: Ghost/OpenClaw only
- `all`: every configured destination when used with `--repo`

Repo selection is optional and defaults to all configured repos. Use
repeatable `--repo` flags to select configured repo names or repo URLs:

```bash
aiskillsync sync main
aiskillsync sync codex
aiskillsync sync openclaw --repo bounty-harness
aiskillsync sync main --repo bounty-harness --repo bounty-tools
aiskillsync sync codex --repo https://github.com/ghostonbutterbread/bug-bounty-harness.git
```

If a `--repo` URL matches an existing configured repo URL, aiskillsync uses
that repo's configured local path and does not create a duplicate clone. An
unconfigured repo URL is treated as an ad-hoc repo for this run only. Its
deterministic clone path is `repo_dir/<repo-slug>-<url-hash>`, so the default is
`~/.config/aiskillsync/repos/<repo-slug>-<url-hash>`. If that path already
exists, normal sync safety checks apply; aiskillsync does not delete or replace
it.

The old repo-first selector syntax remains supported. `sync all` still means all
repos into `sync.default_destinations`, and repeated `--dest` flags still
select legacy destinations:

```bash
aiskillsync sync all
aiskillsync sync bounty-harness
aiskillsync sync 1 2
aiskillsync sync bounty-harness --dest codex --dest claude
```

Focused skill filters:

```bash
aiskillsync sync codex --repo bounty-harness --skill xss
aiskillsync sync main --repo bounty-harness --skill xss --skill bypass
```

Opt-in migration/adoption:

```bash
aiskillsync sync codex --repo bounty-harness --skill xss --adopt --dry-run
aiskillsync sync codex --repo bounty-harness --skill xss --adopt
```

During adoption, `directory-copy`, `unexpected-symlink`, and same-name path
conflicts are moved to a timestamped backup root before the canonical symlink
is created. Without `--adopt`, those entries remain conflicts and block apply.

Migration denylists:

```bash
aiskillsync sync main --repo bounty-harness --adopt --exclude-skill local-private-skill
aiskillsync sync main --repo bounty-harness --adopt --denylist ./migration-denylist.txt
```

`--denylist` files are newline-delimited skill names. Blank lines and `#`
comments are ignored. The config-level `sync.migration_denylist` is always
honored.

Ghost/OpenClaw is not touched unless it is explicitly selected with
`sync ghost`, `sync openclaw`, `--dest ghost`, `--dest openclaw`, `sync all
--repo ...`, or included in `sync.default_destinations`.

Only `sync.mode: symlink` is supported. Dry-run never runs git and never
mutates repo or destination paths; it reports planned `PLAN` clone and pull
steps. Normal `sync` may run `git clone`, including `--branch <branch>` when
a repo branch is configured, for missing selected enabled repo roots with a
repo URL and `sync.clone_if_missing: true`. For existing selected enabled repo
roots with `sync.pull_before_sync: true`, normal `sync` requires the path to
be a git repo and runs `git -C <path> pull --ff-only`; any non-zero pull
blocks the sync before symlink creation. Disabled repos are never cloned or
pulled.

Sync output ends with a concise summary of repo actions, destination actions,
and final status (`applied`, `dry-run`, or `blocked`). Terminal output colors
successful `LINK`/`CLONE`/`PULL` and applied status green, `SKIP`/`PLAN` and
dry-run status yellow/blue, and `ERROR`/`CONFLICT` and blocked status red.
Captured non-TTY output stays plain unless color is forced with `FORCE_COLOR`;
set `NO_COLOR` to disable automatic color.

## Verification

Targeted stdlib smoke tests:

```bash
python3 -m unittest discover -s tests
```

## Legacy personal migration helper

The old one-time helper remains for compatibility, but new work should prefer
first-class `aiskillsync sync --adopt`.

The helper migrates Ryushe's current copied/symlinked Bounty Harness skills
into clean symlinks. On a main machine where
`~/projects/bug_bounty_harness` is missing, the helper reports a planned clone
during dry-runs and `--list-sources`. Use `--apply` to clone Bounty Harness
from `https://github.com/ghostonbutterbread/bug-bounty-harness.git` on branch
`master` before source discovery:

```bash
python3 scripts/migrate_aiskillsync_personal.py --list-sources
python3 scripts/migrate_aiskillsync_personal.py --apply
python3 scripts/migrate_aiskillsync_personal.py --backup-differs --apply
```

By default it processes `~/.codex/skills` and `~/.claude/skills` only.
Ghost/OpenClaw remains excluded unless explicitly selected with `--dest ghost`.
This helper is intentionally source-checkout only and is not exposed as a
packaged console command.
