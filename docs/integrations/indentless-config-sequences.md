# Indentless config sequences integration dossier

- **Status:** review-ready
- **Owner:** Hermes bugfix subagent
- **Branch / owning ref:** `fix/indentless-config-sequences`
- **Worktree:** `/home/ryushe/projects/.worktrees/aiskillsync-indentless-config-sequences`
- **Base commit:** `aea4fd3fb10312beaba0c2d0415344871cbe6f0f` (fetched `origin/beta`)
- **Intended integration target:** `beta` (`origin/beta`)
- **Last updated:** 2026-10-15
- **Latest immutable recovery checkpoint:** this branch's implementation commit (see Git tip; no prior checkpoint)
- **Feature implementation commit(s):** this branch's implementation commit (see Git tip)
- **Inspiration:** papercut `PC-20261014-231712-4610d531`

## Intent and contract

The installed CLI 0.1.19 and fetched beta 0.1.19 reject synthetic PyYAML `safe_dump` output with `bridges:` immediately followed by a same-indent `- name: ...` (exit 2, `could not parse config near line 3`). PyYAML `safe_load` accepts that text. Fix the owning stdlib-only `parse_simple_yaml` mapping boundary: only when a mapping key has no inline value, accept a sequence whose dash shares that key's indentation. Keep the previous nested sequence form, reject a same-indent dash after a populated scalar, and leave config field validation unchanged. No PyYAML runtime dependency or user config edit.

## Evidence and review

- Red: `python3 -m unittest discover -s tests -p test_config_parser.py -v` failed on the original parser for indentless bridges and sibling mapping.
- Green: same focused suite, 3 tests; subsequent full `python3 -m unittest discover -s tests -v`, 115 tests passing (including added nested-profile regression).
- `python3 -m aiskillsync --version`, package `__version__`, pyproject metadata, and built wheel `METADATA` all report `0.1.20`.
- Review boundary: `aiskillsync/config.py`, parser-focused `tests/test_config_parser.py`, `pyproject.toml`, `aiskillsync/__init__.py`, this dossier. No beta merge, push, pipx upgrade, or papercut closure authorized.
- Propagation: owner is fetched beta; no affected descendant identified for this parser fix. Feature is not integrated; beta remains unchanged.

## Blockers and deferred work

- **Integration / activation:** explicitly excluded from this task. A reviewer should fetch `origin/beta`, compare feature diff, run full tests and wheel metadata check, then decide whether to integrate. Do not treat source build as installed pipx activation.
- **Independent review:** not performed by this subagent; parent/release gate owns it.

## Interruption / resume handoff

- **Owning feature branch/ref:** `fix/indentless-config-sequences`
- **Latest immutable recovery checkpoint:** implementation commit at branch tip
- **Exact resume point:** review diff and receipts, reconcile if beta advances; no merge/push/closure done.
- **Working-tree state at handoff:** clean after commit (verify before handoff).

## Decision gates

- **Integration:** pending independent review; no merge requested here.
- **Activation / promotion:** not requested; installed CLI remains 0.1.19.

## Decision record

- 2026-10-15 — reproduced on synthetic config; parser fix verified locally and held on isolated feature branch.
