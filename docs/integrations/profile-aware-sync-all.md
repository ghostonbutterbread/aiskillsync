# Profile-aware `sync all`

## Intent

Make configured profile sets the authoritative projections for `aiskillsync sync all`.
Hermes task roles can whitelist category subtrees while broad CLI roles such as
Claude and Codex use `include_categories: all` and exclude only prohibited
subtrees such as `career`.

## Base and target

- Base: `5d161d1` (`feat/category-authoring-contract`)
- Target: `beta`
- Feature branch: `feat/profile-aware-sync-all`

## Implemented contract

- A profile set may target any configured destination, not only Hermes.
- When profile sets exist, `sync all` expands every profile independently and
  never broadcasts all bridges to every destination.
- `sync --profile <name>` uses the same projection behavior.
- `include_categories: all` explicitly selects all categories on a categorized
  bridge; `exclude_categories` removes category subtrees afterward.
- Category filters, including `all`, are invalid on flat bridges.
- Every configured destination must be assigned to a profile set while profile
  sets are active; ordinary destination/repository selector forms are rejected
  to prevent bypassing the projection policy.
- Configurations without profile sets retain legacy broad `sync all` behavior.

## Evidence

- Focused `include_categories: all` test: passed.
- Full suite: `python3 -m unittest discover -s tests -v` — 91 tests passed.
- `python3 -m py_compile aiskillsync/*.py tests/*.py` — passed.
- `git diff --check` — clean.
- Source-tree CLI: `python3 -m aiskillsync --version` — `0.1.9`.

## Activation boundary

No live `~/.config/aiskillsync/config.yaml` or runtime destinations were changed.
After review/integration, define all destinations through profile sets, dry-run
`sync all`, audit local shadowing for each Hermes profile, then apply and start
fresh Hermes sessions.
