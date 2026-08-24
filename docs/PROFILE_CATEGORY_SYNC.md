# Profile-Scoped Category Sync — Draft Design

**Status:** draft proposal; no runtime behavior changes in this document.

## Goal

Allow aiskillsync to project only selected **source-directory categories** into
a named agent profile. A profile receives a small, reviewable skill catalog that
continues to resolve every selected skill to its canonical Git source.

This addresses a profile such as `jobsearch`: it should receive browser
research, career, productivity, and communications skills without inheriting
security-hunting or coding policies merely because those skills exist elsewhere
in the shared catalog.

## Problem

Today aiskillsync discovers only the immediate children of a bridge's
`skills_path` and projects enabled bridges into a destination root. Hermes
profiles commonly share one external root, such as `~/.hermes/synced-skills`.
A cloned profile can also contain copied local skills, which may shadow a synced
canonical skill because Hermes resolves local skills before external roots.

This is correct for a single universal catalog, but it does not express:

- a canonical category for a skill;
- which categories belong to an agent role;
- a separate projected root for each Hermes profile; or
- a safe migration from copied local skills to canonical symlinks.

## Design Principles

1. **Canonical source owns classification.** A skill's category is its relative
   directory beneath the source bridge's `skills_path`; do not maintain a second
   per-skill category table.
2. **Profiles select categories, not hand-maintained lists.** An exception list
   is allowed for a rare one-off, but ordinary role selection is directory-based.
3. **Selections are source-qualified.** `career` in `general-skills` is not the
   same thing as an identically named directory in another bridge.
4. **Projection preserves provenance.** Every projected entry is a symlink to a
   specific canonical source skill directory; the sync receipt names that source.
5. **No implicit deletion.** A profile selection change must never remove a
   copied local skill, an unmanaged path, or a symlink outside the previously
   managed profile projection.
6. **Safety policies remain deliberately assigned.** A category is organization,
   not permission to apply an unrelated policy. BBH remains the owner of
   security-hunting skills and ai-policies remains the owner of policy skills.
7. **Stable/beta lane selection happens before category selection.** A profile
   never combines two variants of one lane group.

## Canonical Repository Layout

Skill repositories may organize skills in nested directories:

```text
general-skills/
  skills/
    core/
      safe-fetch/SKILL.md
      coordination/SKILL.md
      papercuts/SKILL.md
    browser-research/
      grounded-citations/SKILL.md
      browser-operations/SKILL.md
    career/
      job-search/SKILL.md
      job-posting-analysis/SKILL.md
      application-tracking/SKILL.md
    productivity/
      document-workflows/SKILL.md
      spreadsheets/SKILL.md
    communications/
      email-triage/SKILL.md
```

The category is the source-relative directory prefix. For example,
`career/job-search` belongs to the `career` category under the configured
`general-skills` bridge.

Skill `name` values remain globally unique across all selected bridges. The
syncer must reject ambiguous duplicate names even if their source directories
are different.

## Proposed Configuration

Add two optional top-level configuration sections:

```yaml
profile_sets:
  jobsearch:
    destination: hermes-jobsearch
    sources:
      - bridge: general-skills-beta
        include_categories:
          - core
          - browser-research
          - career
          - productivity
          - communications
    include_skills: []
    exclude_skills: []

  coder:
    destination: hermes-coder
    sources:
      - bridge: general-skills-beta
        include_categories: [core, browser-research, productivity, operations]
      - bridge: ai-policies
        include_categories: [coding]

  hunter:
    destination: hermes-hunter
    sources:
      - bridge: bounty-harness
        include_categories: [security]
      - bridge: ai-policies
        include_categories: [security]
      - bridge: general-skills-beta
        include_categories: [core, operations]

ai_skill_paths:
  hermes-jobsearch: ~/.hermes/profiles/jobsearch/synced-skills
  hermes-coder: ~/.hermes/profiles/coder/synced-skills
  hermes-hunter: ~/.hermes/profiles/hunter/synced-skills
```

`include_categories` values are relative to the named bridge's `skills_path`.
A value selects its full subtree. An omitted `include_categories` preserves
current bridge-wide sync behavior for backward compatibility.

`include_skills` and `exclude_skills` are source-qualified identifiers in the
form `bridge:relative/path` and are evaluated after category selection:

- `include_skills` adds a narrowly justified exception;
- `exclude_skills` removes a selected skill; and
- both are reported in the receipt.

## Proposed CLI

```bash
# Preview only the job-search projection.
aiskillsync sync --profile jobsearch --dry-run

# Apply the selected profile projection.
aiskillsync sync --profile jobsearch

# Validate category paths, selected skills, profile destinations, and conflicts.
aiskillsync doctor --profile jobsearch

# List resolved categories and profile membership without filesystem changes.
aiskillsync list --profile jobsearch
```

Existing `sync`, `--repo`, `--dest`, `--skill`, `--exclude-skill`, and lane
commands retain their current behavior. `--profile` is a selector for a named
configuration profile set, not a Hermes runtime command and not a replacement
for `--dest`.

## Projection Rules

1. Resolve active stable/beta lanes.
2. Materialize and validate each selected bridge once.
3. Recursively discover `SKILL.md` directories, recording each skill's relative
   path, leaf name, and bridge identity.
4. Resolve the selected category subtrees and explicit exceptions.
5. Reject missing categories, malformed skill directories, duplicate selected
   skill names, duplicate destination names, and ambiguous source-qualified
   identifiers before destination mutation.
6. Create only missing symlinks in the named profile destination during normal
   sync.
7. Prune only stale symlinks recorded as managed by the same profile set and
   resolving beneath its formerly selected source roots. Unmanaged paths remain
   conflicts.
8. Emit a receipt containing the selected profile, active lanes, bridge commits,
   category paths, selected skills, exceptions, link actions, skips, prunes, and
   conflicts.

The destination layout may preserve category directories, for example:

```text
~/.hermes/profiles/jobsearch/synced-skills/
  core/safe-fetch -> <canonical source>
  career/job-search -> <canonical source>
```

Before implementation, verify Hermes resolves nested external skill paths in a
fresh profile session. If it does not, the syncer may flatten only the managed
projection while retaining source-relative category metadata in its manifest;
it must still reject leaf-name collisions.

## Hermes Profile Integration

Each Hermes profile must point `skills.external_dirs` at its own projected
root, using Hermes configuration commands rather than manual YAML edits:

```bash
hermes -p jobsearch config set skills.external_dirs \
  ~/.hermes/profiles/jobsearch/synced-skills
```

A fresh Hermes session is required after changing external skill configuration.
The migration does not delete the profile's local `skills/` tree automatically.
After a profile projection is verified, compare potential local duplicates with
the canonical source and use explicit, backed-up adoption only for byte-verified
matches.

## Migration Plan

### Phase A — Design and inventory

- Add recursive, source-relative discovery in aiskillsync behind tests.
- Inventory existing canonical repositories and classify proposed directories.
- Record owner boundaries: BBH for security workflows, ai-policies for policies,
  general-skills for cross-domain reusable capabilities.
- Do not move skills or alter live profile configuration in this phase.

### Phase B — Profile-set engine

- Parse and validate `profile_sets`.
- Add `sync --profile`, `list --profile`, and `doctor --profile`.
- Add a managed projection manifest for stale-link reconciliation.
- Preserve all existing bridge-wide commands and receipts.

### Phase C — Isolated fixture tests

- Use temporary Git repositories with nested skill categories and one selected
  profile destination.
- Cover category inclusion, subtree selection, explicit include/exclude,
  duplicate leaf rejection, unknown category rejection, dry-run immutability,
  stale managed-link pruning, and unmanaged-path protection.
- Run a fresh Hermes fixture profile against the projected root to prove runtime
  skill discovery.

### Phase D — Controlled live rollout

- Define only the `jobsearch` profile set first.
- Create its profile-owned projection with a dry-run, then apply after review.
- Configure `jobsearch` to use that projection and validate a fresh session.
- Compare copied local skills with the canonical projection; adopt only exact
  matches with backups.
- Keep the existing global Hermes projection unchanged until the controlled
  profile run is clean.

### Phase E — Repository category migration

- Move general-skills into reviewed category directories in a dedicated change.
- Update contributor guidance so a new skill must select its canonical category.
- Update BBH and ai-policies only where their current flat layout needs a
  category boundary; do not reorganize solely for aesthetics.
- Add `coder` and `hunter` profile sets after their category selections are
  reviewed.

## Non-Goals

- No automatic policy inference from an agent name.
- No copying of skills, credentials, memories, or browser profiles between
  Hermes profiles.
- No mutation of a profile's local skills without explicit adoption.
- No merging stable and beta variants into the same projection.
- No separate job-agent repository until job-specific application code or data
  contracts warrant one.
- No change to existing default sync behavior unless a profile set is explicitly
  selected.

## Acceptance Criteria

The feature is ready for a controlled jobsearch rollout only when:

- category discovery is recursive and rejects invalid source directories;
- `sync --profile jobsearch --dry-run` reports an exact, stable selected set;
- a real sync creates only profile-owned canonical symlinks;
- rerunning the same sync is a no-op;
- an unrelated local skill or unexpected symlink blocks rather than being
  replaced;
- changing a profile selection prunes only recorded managed stale links;
- `doctor --profile` identifies category, bridge, lane, and destination drift;
- a fresh `jobsearch` Hermes session resolves a representative skill from every
  selected category; and
- no profile shares another profile's external projection unless explicitly
  configured to do so.
