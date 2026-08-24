# External skills

- **Status:** ready for review
- **Branch:** `feat/external-skills`
- **Base:** `ebca8b6b1fe91b4dd65bb0e75fddb9c4a70e095d` (local `main`)
- **Target:** repository integration branch to be selected during reconciliation; local `main` is currently diverged from `origin/main`.

## Intent

Keep externally maintained skills distinct from team-owned source checkouts while preserving ordinary automatic sync behavior.

## Implemented contract

- `aiskillsync external add <repo-url>` records a bridge with `kind: external`.
- The default checkout path is `repo_dir/external/<name>`.
- `--skills-path` supports nested skill layouts such as the Android reverse-engineering plugin.
- External bridges use the existing clone/pull/symlink sync flow without changing existing managed bridges.

## Evidence

- Focused CLI test covers add, config persistence, nested skill discovery, and symlink projection.
- Full suite: `python3 -m unittest discover -s tests -v` — 79 passing.
- CLI/version smoke: `python3 -m aiskillsync --version` reports `0.1.6`; `external --help` and bytecode compilation pass.

## Deferred

`TODO.md` records the next slice: review external diffs with deterministic indicators and an AI gate before promotion. This feature intentionally does not gate the existing automatic update path.

The review receipt now explicitly includes the selected `skills_path`, its
plugin/marketplace provenance, and the complete discovered skill list.

## Activation boundary

The feature is not installed into the current pipx aiskillsync runtime. It becomes available after review/integration, push, and `pipx upgrade aiskillsync` on each host.
