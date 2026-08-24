# External skills

- **Status:** ready for review
- **Branch:** `feat/external-skills`
- **Base:** `8dea009` (`origin/main`, including merged profile projections)
- **Target:** repository integration branch to be selected during reconciliation.

## Intent

Keep externally maintained skills distinct from team-owned source checkouts while preserving ordinary automatic sync behavior.

## Implemented contract

- `aiskillsync external add <repo-url> --profile <name>` records a bridge with `kind: external` and assigns it to an existing profile set.
- `--profile all` deliberately expands to every configured profile set; it cannot be mixed with named profiles.
- `--skill <name>` is repeatable and records a profile-source `include_skills` filter; the next sync validates that each named skill exists below the source's declared `skills_path`.
- The default checkout path is `~/.config/aiskillsync/external/plugins/<name>` (or `external/plugins/<name>` beside a custom config file).
- `--skills-path` supports nested skill layouts such as the Android reverse-engineering plugin.
- External bridges use the existing clone/pull/symlink sync flow without changing existing managed bridges.

## Evidence

- Focused CLI tests cover external registration, profile-all assignment, config persistence, nested skill discovery, and symlink projection.
- Full suite: `python3 -m unittest discover -s tests -v` — 105 passing.
- CLI/version smoke: `python3 -m aiskillsync --version` reports `0.1.15`; `external add --help` and bytecode compilation pass.
- End-to-end fixture: a real `file://` Git source with `alpha` and `beta` skills cloned into the external plugins cache; `--skill alpha` projected only `alpha` into the `hunter` profile.

## Deferred

`TODO.md` records the next slice: review external diffs with deterministic indicators and an AI gate before promotion. This feature intentionally does not gate the existing automatic update path.

The review receipt now explicitly includes the selected `skills_path`, its
plugin/marketplace provenance, and the complete discovered skill list.

## Activation boundary

The feature is not installed into the current pipx aiskillsync runtime. It becomes available after review/integration, push, and `pipx upgrade aiskillsync` on each host.
