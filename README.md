# aiskillsync

Global AI skill synchronization tooling.

This repo is intended to replace per-project `sync_skills.sh` scripts with one
config-driven sync layer that can bridge skills from multiple repos into AI
provider skill directories.

## Profile-scoped category sync

`aiskillsync` supports both flat and categorized source bridges. A categorized
bridge recursively discovers `SKILL.md` files and lets a named profile set select
a full category subtree. Profile sets are destination projections for any target
(including Codex and Claude), not just Hermes profiles. A flat bridge keeps the
existing direct-child behavior.
See [Profile-scoped category sync](docs/PROFILE_CATEGORY_SYNC.md) for the
configuration and migration contract.

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
aiskillsync sync --profile jobsearch --dry-run
aiskillsync sync --profile jobsearch
aiskillsync sync codex --repo bounty-harness --skill xss --adopt --dry-run
aiskillsync sync all --overwrite
aiskillsync root-context main --repo ai-policies
aiskillsync root-context codex --repo ai-policies --dry-run
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
- opt-in `sync --overwrite` repo force-refresh plus destination adoption
- managed Codex/Claude root-context sync from bridge `root-context/` templates

`sync` applies by default. The compatibility `--apply` flag is accepted but no
longer required. Apply is intentionally narrow in Phase 3: it may first clone
missing selected enabled repo roots when `repo` exists
and `sync.clone_if_missing` is true, or refresh existing selected enabled git
repo roots when `sync.pull_before_sync` is true. Independent checkout roots are
materialized concurrently (three workers by default); bridges sharing a checkout
remain serialized. Once every selected source is ready, the resulting inventory
is planned once and fanned out to the requested destinations. It then creates
destination symlinks that are missing. Existing correct symlinks are skipped, and existing
directories, files, or symlinks to unexpected targets are conflicts that block
the whole apply. It does not delete, adopt, back up, or replace destination
entries unless `--adopt` or `--overwrite` is explicitly passed. When a Git
refresh changes a source revision, aiskillsync uses `git diff --name-status`
under that bridge's `skills_path` to report changed skills; it does not walk and
hash every skill file.

`--adopt` is migration mode, not normal sync. It backs up same-name destination
entries under `~/.cache/aiskillsync-migration/<timestamp>/...` and replaces
them with symlinks to selected repo skills. Prefer pairing it with
`--skill <name>` or a denylist so migrations stay focused.

`--overwrite` is the stronger clean-start mode. It includes destination
adoption behavior and, when `sync.pull_before_sync: true`, force-refreshes
selected git repos with `git fetch --prune`, `git reset --hard origin/<branch>`
when a branch is configured or `@{upstream}` otherwise, and `git clean -fd`
before linking skills. Use `--dry-run --overwrite` first to see exactly which
repos would be reset.

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
    skill_layout: flat
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
  materialization_workers: 3
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

For an ordinary skill repository, add one bridge and sync it. It remains on its
configured branch; it does not need a lane unless stable/beta alternatives must
be selectable.

Add a repo URL to the config. If no local location is given, aiskillsync uses
`repo_dir/<repo-name>`; by default `repo_dir` is
`~/.config/aiskillsync/repos`. This updates config only; the repo is cloned on
the next `sync` when selected, unless `--dry-run` is used.

```bash
aiskillsync add https://github.com/org/ai-skills.git
aiskillsync add https://github.com/org/ai-skills.git ~/projects/ai-skills
aiskillsync repo add https://github.com/org/ai-skills.git --name ai-skills --branch main
aiskillsync add ~/projects/local-skill-repo
aiskillsync external add https://github.com/example/third-party-skills.git --profile hunter --skills-path plugins/example/skills
```

Remove a repo from config without deleting its local checkout or any provider
skill symlinks:

```bash
aiskillsync remove ai-skills
aiskillsync repo remove https://github.com/org/ai-skills.git
```

## External Skill Sources

Use `external add` for an upstream-maintained skill repository. It behaves like
an ordinary bridge during sync—so a normal profile sync fetches the configured
branch and updates the linked runtime skill—but records `kind: external`, keeps
the default checkout under `~/.config/aiskillsync/external/plugins/<name>` (or
next to a custom config file), and requires an explicit `--profile` routing
decision. This separates upstream dependencies from team-owned source checkouts
without changing the runtime symlink model.

```bash
aiskillsync external add https://github.com/SimoneAvogadro/android-reverse-engineering-skill.git \
  --name android-reverse-engineering \
  --profile hunter \
  --skills-path plugins/android-reverse-engineering/skills \
  --skill android-reverse-engineering \
  --branch master

# Deliberately route a source to every configured profile set.
aiskillsync external add https://github.com/example/shared-skills.git --profile all

# Then preview or apply only the intended profile projection.
aiskillsync sync --profile hunter --dry-run -v
```

`skills_path` names the directory whose immediate child directories are skills.
Each child that contains `SKILL.md` is projected separately to every selected
agent runtime, together with its references and scripts. A normal skills
repository therefore needs no option (`skills_path: skills` is the default);
plugin and marketplace repositories use `--skills-path` to select the nested
skill root deliberately.

`--skill` is optional and repeatable. When omitted, every skill discovered
directly under `skills_path` is assigned to the selected profile. When supplied,
only those named child directories are assigned—for example, `--skill
android-reverse-engineering` selects
`<skills_path>/android-reverse-engineering/SKILL.md` and does not project its
sibling skills. The name is checked at the next sync after the external source
has been cloned; an unknown name blocks the profile sync rather than silently
installing a different skill.

The explicit root is part of external-source review: reviewers must confirm it
matches the intended plugin/marketplace entry and enumerate the discovered
skill names before activation. This permits one upstream source to supply
multiple skills without recursively importing unrelated `SKILL.md` files.

## Agent-file synchronization

Agent definitions are opt-in **files**, separate from skill directories. Use
`agents` to project Claude-compatible definitions from BBH's canonical `agents/`
directory without running skill sync or changing its projection manifest:

```yaml
bridges:
  - name: bounty-harness
    path: ~/projects/bug_bounty_harness
    skills_path: skills
    agents_path: agents

ai_skill_paths:
  claude: ~/.claude/skills
ai_agent_paths:
  claude: ~/.claude/agents

sync:
  pull_before_sync: false
  clone_if_missing: false
```

```bash
aiskillsync agents --repo bounty-harness --dest claude --dry-run
aiskillsync agents --repo bounty-harness --dest claude
aiskillsync agents --repo bounty-harness --dest claude --agent reviewer --adopt --dry-run
```

Only immediate `*.md` children with opening/closing `---` frontmatter, a valid
top-level `name`, and a non-empty top-level `description` are discovered. Names
start with a lowercase letter or digit and contain only lowercase letters,
digits, and hyphens. Required fields accept the config parser's scalar subset;
descriptions also accept indented `|`/`>` blocks. Other provider fields and the
body are not transformed or schema-validated. Invalid files and Markdown without
agent frontmatter (such as `index.md`) are reported as skipped; code files and
nested directories are not scanned. Missing configured source directories,
no valid definitions, missing `--agent` selections, and duplicate selected agent
names block apply. Destination filenames use the frontmatter name:
`<ai_agent_paths.claude>/<name>.md` links to the original source file, even when
its source filename differs.

There are **no inferred agent roots** and no built-in Codex/Hermes conversion.
Claude's `~/.claude/agents` is the primary supported format/consumer. Other roots
can be explicitly configured only when their consumer accepts the source format;
configuring a path does not establish compatibility. Agent roots cannot alias
another agent or skill destination root.

If `profile_sets` is active, `agents` requires a named profile instead of
`--repo`/`--dest`, so it cannot bypass existing source/destination ownership:

```yaml
profile_sets:
  security:
    destinations: [claude]
    sources:
      - bridge: bounty-harness
```

```bash
aiskillsync agents --profile security --dry-run
aiskillsync agents --profile security --agent reviewer --adopt
```

A profile uses its listed bridges that explicitly configure `agents_path`, and
only its destinations present in `ai_agent_paths`. Skill category/name filters
apply only to skills; use repeatable `--agent <frontmatter-name>` for a focused
agent run. A destination can exist in either or both path mappings; with profiles
active, every configured destination name in either mapping must be assigned to
exactly one profile. Skill sync ignores agent-only destinations, and `agents`
never links skills. Without profiles, `--dest` is required (repeatable); `--repo`
accepts existing configured bridge selectors and defaults to all configured
bridges with `agents_path`. Ad-hoc source URLs are not added by this command.

The active lane and configured branch checks are shared with skill sync. Clone
and fast-forward pull use the existing `sync.clone_if_missing` and
`sync.pull_before_sync` settings, only for selected agent-enabled sources.
Git refreshes the whole selected checkout, so existing skill links into that
checkout can see updated source content too; set `pull_before_sync: false` for
an isolated rollout of already-reviewed local agent files.
`--dry-run` never clones, pulls, creates a default config, writes backups, or
changes links. A missing cloneable source reports deferred discovery; preview
again after cloning to inspect its definitions. Branch checks may run read-only
Git commands. Apply requires an existing explicit config or existing default
config; agent support is not enabled by the default template.

Correct links are no-ops. Same-name files, directories, and unexpected/dangling
symlinks block the entire destination apply unless `--adopt` is explicitly used.
Adoption moves the entry (not a symlink's target) to
`~/.cache/aiskillsync-migration/<timestamp>/agents/<destination>/<name>.md` before
linking; existing backup paths are never overwritten. Apply rechecks destination
state and source branches before writing. If linking an adopted entry fails, it
attempts to restore that entry. A multi-file filesystem failure can still leave
earlier successful links in place; inspect the reported error and backups.

Agent sync never prunes anything: filtered-out entries, unmanaged files, and
obsolete links are preserved. Lane `switch` remains a skill/launcher operation;
run `agents --profile <name> --dry-run` after a switch, then explicitly adopt any
old-lane links you want to repoint. Removing/renaming old agents is a separate
manual cleanup decision, not an implicit consequence of a filtered rollout.

## Policy Lanes: stable and beta

A **bridge** is one checked-out skill source. For a repository that needs a
stable and beta policy lane, configure two bridges at separate worktree or
clone paths, then place them in one mutually-exclusive lane group:

```yaml
bridges:
  - name: ai-policies-stable
    repo: https://github.com/example/ai-policies.git
    path: ~/projects/ai-policies-stable
    branch: main
    skills_path: skills
    enabled: true
  - name: ai-policies-beta
    repo: https://github.com/example/ai-policies.git
    path: ~/projects/ai-policies-beta
    branch: beta/policies
    skills_path: skills
    enabled: true

lanes:
  policies:
    default: stable
    variants:
      stable: ai-policies-stable
      beta: ai-policies-beta
```

Only the active bridge for each lane group participates in normal `sync`.

### BBH shared launcher projection

BBH is the one application integration currently supported alongside skill
projection. Bind its local-only launcher path to the one lane group that owns
the separate stable/beta BBH checkouts:

```yaml
bbh_launcher_path: ~/.local/bin/bbh
bbh_launcher_lane_group: bbh
```

Both settings are required together, and the lane-group name must exist. Only a
successful switch of that named group atomically repoints the symlink to the
selected checkout's `scripts/bbh`; switching any other lane leaves it unchanged.
The launcher then resolves BBH scripts and its checkout-local `.venv` itself.
Do not use these settings for ordinary skill repositories or as a general
dependency manager.

Repositories outside `lanes` retain the ordinary configured-branch behavior.
A branch-configured bridge must be checked out on its configured branch before
sync can pull, discover, or link skills; a mismatch blocks the run.

Switch the active selection and reconcile its destination symlinks with:

```bash
aiskillsync switch beta --dry-run      # when exactly one lane group exists
aiskillsync switch beta
aiskillsync switch policies stable     # named group form
```

After a successful non-dry-run switch, aiskillsync atomically writes one
`active-stack.json` beside the config. It is both the canonical saved lane
selection and the generated, host-local runtime receipt. For compatibility,
existing selection-only `lanes-state.json` files remain readable until the next
successful switch. The receipt records every active lane group's selected bridge,
resolved checkout root, configured branch, and current Git revision. It is the
runtime integration input for optional cross-repository consumers; shared
configuration continues to name logical bridges rather than another machine's
absolute paths. A switch repoints shared skill names, and removes only stale
symlinks that still point under the previously active lane source. It never
deletes ordinary folders, local skill copies, or symlinks outside that prior lane
source.

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

## Root Context Command

`root-context` syncs managed provider root-context templates from a configured
bridge into the provider root files. The source files live under the bridge's
`root-context/` directory:

```text
root-context/CODEX.md
root-context/CLAUDE.md
```

The provider targets are inferred from `ai_skill_paths`: Codex writes beside
`~/.codex/skills` as `~/.codex/CODEX.md`, and Claude writes beside
`~/.claude/skills` as `~/.claude/CLAUDE.md`.

Examples:

```bash
aiskillsync root-context main --repo ai-policies
aiskillsync root-context codex --repo ai-policies --dry-run
```

The command replaces only the `AIPOLICIES_MANAGED` block when the target file
already has one. If the target exists without that block, the managed block is
appended so local provider-specific context is preserved.

The old repo-first selector syntax remains supported. Without `profile_sets`,
`sync all` means all repos into `sync.default_destinations`, and repeated
`--dest` flags still select legacy destinations:

```bash
aiskillsync sync all
aiskillsync sync bounty-harness
aiskillsync sync 1 2
aiskillsync sync bounty-harness --dest codex --dest claude
```

When `profile_sets` is configured, each named entry is a shared policy or
capability group. `sync all` expands every group to each configured destination,
while `sync --profile security` applies that complete group to all of its
destinations. Configure shared groups with plural `destinations` (legacy singular
`destination` remains accepted):

```yaml
profile_sets:
  security:
    destinations:
      - claude
      - codex
      - hermes-security
    sources:
      - bridge: security-policies
```

Every `ai_skill_paths` destination must be assigned exactly once across profile
groups; unknown, duplicate, or competing destination assignments fail validation.
A group may intentionally declare `destinations: []` as a dormant template for a
future agent; `sync all` skips it, and a focused `sync --profile <name>` reports
that it has no destinations rather than falling back to default destinations.
After a successful non-dry-run profile sync, `profile-projections.json` beside
the config records that profile's canonical managed links. Removing a source
later prunes only a still-matching recorded link; dry runs never write this
manifest and first use never guesses legacy ownership. Other destination/repo
selector forms are rejected while profile sets are active,
which avoids bypassing their policy. Prefer repository-level selection: omit a
dedicated job-search bridge from Claude/Codex rather than filtering a
general-purpose bridge. For categorized bridges, omitting `include_categories`
selects every category; provide it only as a whitelist. `include_categories: all`
remains accepted for compatibility, and `exclude_categories` still applies when
inclusion is omitted. Category selectors are invalid on flat bridges.

Focused skill filters:

```bash
aiskillsync sync codex --repo bounty-harness --skill xss
aiskillsync sync main --repo bounty-harness --skill xss --skill bypass
```

Opt-in migration/adoption:

```bash
aiskillsync sync codex --repo bounty-harness --skill xss --adopt --dry-run
aiskillsync sync codex --repo bounty-harness --skill xss --adopt
aiskillsync sync all --overwrite --dry-run
aiskillsync sync all --overwrite
```

During adoption, `directory-copy`, `unexpected-symlink`, and same-name path
conflicts are moved to a timestamped backup root before the canonical symlink
is created. Without `--adopt`, those entries remain conflicts and block apply.
`--overwrite` includes this destination adoption behavior and also resets
selected git repos back to their remote state when pull-before-sync is enabled.

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
mutates repo or destination paths; it reports planned `PLAN` clone, pull, or
force-refresh steps. Normal `sync` may run `git clone`, including
`--branch <branch>` when a repo branch is configured, for missing selected
enabled repo roots with a repo URL and `sync.clone_if_missing: true`. For
existing selected enabled repo roots with `sync.pull_before_sync: true`, normal
sync requires the path to be a git repo and runs `git -C <path> pull
--ff-only`; any non-zero pull blocks the sync before symlink creation.
With `--overwrite`, that pull is replaced by `fetch --prune`, `reset --hard`,
and `clean -fd`; any non-zero command blocks the sync before symlink creation.
Disabled repos are never cloned, pulled, or force-refreshed.

Sync output ends with a concise summary of repo actions, destination actions,
and final status (`applied`, `dry-run`, or `blocked`). Terminal output colors
successful `LINK`/`CLONE`/`PULL`/`FORCE` and applied status green,
`SKIP`/`PLAN` and dry-run status yellow/blue, and `ERROR`/`CONFLICT` and
blocked status red.
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
