# Profile-group fan-out

## Status

Branch-local feature dossier for `feat/profile-groups`. Do not merge this
handoff document into an integration branch.

## Intent and boundaries

Turn `profile_sets` into shared policy/capability groups that project identical
selected sources to multiple configured destinations. Preserve the legacy
singular `destination` input shape, preserve no-profile legacy `sync all`, and
do not touch live configuration or destinations.

- Base: `79b6056` (`feat/profile-aware-sync-all`)
- Target: integration branch selected by the owner
- Implementation commit: pending (user requested no commit)

## Implemented contract

- `destinations` is the preferred plural profile schema; a legacy singular
  `destination` normalizes to a one-element destination tuple. An explicit
  empty plural list defines a dormant future-agent template and is skipped by
  `sync all`.
- Profiles reject both forms together, neither form, invalid list values,
  unknown or duplicate names, competing group assignments, and unassigned
  configured destinations while groups are active.
- `sync all` and `sync --profile <name>` fan the group source selection out to
  every group destination.
- Omitted `include_categories` selects all categories from categorized bridges;
  exclusions still apply. `include_categories: all` remains accepted. Flat
  bridges continue to reject category selectors.
- Serializer writes plural `destinations`; docs prefer omission over explicit
  `include_categories: all` for broad categorized sources.

## Evidence

- Profile group fan-out and category coverage passed before lifecycle hardening.
- Lifecycle regression coverage proves source removal is pruned only after it
  was successfully recorded, an unrelated link survives, and another profile
  group remains untouched.
- Canonical shared destination roots across profile groups are rejected.
- Final full-suite and source-version receipts are recorded before integration.

## Lifecycle state

Successful profile projections write non-secret managed-link receipts to
`profile-projections.json` beside the selected configuration. A later source
removal can prune only a still-matching recorded target. Dry runs do not write
state, and an upgrade does not infer ownership from legacy links.

## Activation boundary

No files under `~/.config`, no installed package, no runtime destinations, no
commit, and no push were changed. After review, use a copied config to dry-run
`sync all`, inspect each destination projection, then intentionally activate
through the normal integration/release workflow.
