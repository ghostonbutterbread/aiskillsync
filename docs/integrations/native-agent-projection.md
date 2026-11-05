# Native agent projection integration dossier

- **Status:** feature checkpoint; integration blocked on parent review and deferred runtime evidence
- **Owner:** delegated native-agent-projection agent
- **Branch:** `feat/native-agent-projection`
- **Worktree:** `/home/ryushe/worktrees/aiskillsync-native-agent-projection`
- **Base commit:** `d3e254d5f61a8ecad61964b590092090c2e7ecc6` (`feat/sync-all-agents`)
- **Intended integration target:** `feat/sync-all-agents` (parent-owned; not beta directly)
- **Last updated:** 2026-11-05
- **Owning feature branch/ref:** `feat/native-agent-projection`
- **Latest immutable recovery checkpoint:** base `d3e254d5f61a8ecad61964b590092090c2e7ecc6`; implementation at this dossier's containing commit (see branch tip)
- **Feature implementation commits:** this dossier's containing commit; resolve via `git log feat/sync-all-agents..feat/native-agent-projection`
- **Canonical references:** OpenAI Codex subagents documentation, OpenCode agents documentation, OMP `packages/coding-agent/src/discovery/helpers.ts` and `src/task/spawn-policy.ts` upstream; see README links.

## Intent and contract

Add explicitly configured, source-prompt-preserving generated files for Codex standalone agent TOML and OpenCode subagent Markdown without changing legacy raw link behavior, profiles, lane selection, or live configurations. Models are not pinned. OpenCode output sets `permission.task: deny`; Codex inherits the parent's permissions and relies on source instructions / runtime limits rather than claiming equivalent enforcement. Native conversion rejects source fields besides name and description, and conflicts with existing/edited files even under adoption. Generated ownership markers protect updates; no stale native-file pruning. Source and destination are rechecked before writing; multi-file application is not a transaction. Different-source/lane ownership requires explicit reconciliation.

## Evidence and review

- Focused: `OPENCODE_TEST_BIN=<isolated opencode 1.19.5 binary> python3 -m unittest discover -s tests -p test_agents.py -v`; 30 passed including actual isolated OpenCode `agent list` loader discovery. No live profile, credentials, model, or target traffic.
- Full: `OPENCODE_TEST_BIN=<same> python3 -m unittest discover -s tests -v`; 145 passed, with HOME/XDG paths inside private scratch. Re-run after any merge.
- Codex: stdlib `tomllib` parses generated TOML and checks instructions/model omission; native Codex loader/spawn not exercised. `python3 -m aiskillsync --version` and wheel metadata report 0.1.28.
- Independent review: pending; parent owns integration decision. No push, merge or activation.

## Blockers and deferred work

1. **OMP safety and per-invocation routing:** upstream `parseAgentFields` calls `parseArrayOrCSV([])` which yields `undefined`; `resolveSpawnPolicy(undefined)` is unrestricted. Thus existing `spawns: []` cannot be assumed effective, and projecting it would make a false safety claim. Exact installed Hoster OMP revision and behavior are unverified. Need an upstream-fixed or otherwise tested no-delegation contract, plus reconciliation of local prompt/tool/skill-preload fields and a version-matched isolated native loader + spawn-policy test before adding an OMP format. Do not overwrite local OMP definitions; legacy raw-link adoption remains separately dangerous and parent must evaluate it.
2. **Codex loader:** current OpenAI docs specify standalone TOML auto-discovery, but installed host version and native invocation were not verified. In a disposable credential-free `CODEX_HOME`, use the exact intended Codex version and its actual discovery interface to confirm role name, instructions and inherited settings; if it only supports config-registered roles, registration must be separately designed and tested. A `tomllib` parse does not establish discovery.
3. **OpenCode semantics:** loader discovery is verified in fixture only; exact target version, child prompt/permission enforcement and model routing per spawn are not verified. Use a disposable fixture and non-network test provider/loader API before activation. Per-call model choice is runtime/version dependent; unpinned is not equivalent to per-call support.
4. **Pi / Claude:** no Pi extension contract was verified, and Claude remains legacy raw Markdown only, not an adapted projection; do not report native compatibility for a path alone. Review exact extension version and Claude loader before any claimed end-to-end coverage.
5. **Activation:** no Hoster writes or runtime configuration changes are authorized by this branch. Parent must inspect same-name definitions and migrate their distinct bodies, preloads, and safety constraints explicitly; backups alone do not reconcile them. Preserve unresolved files and report conflicts.

## Interruption / resume handoff

- **Owning feature branch/ref:** `feat/native-agent-projection`
- **Latest immutable recovery checkpoint:** implementation checkpoint at branch tip; `git rev-parse feat/native-agent-projection`
- **Exact resume point:** independently review diff against base, re-run tests on merged candidate, resolve deferred provider checks; parent decides whether to integrate.
- **Working-tree state at handoff:** expected clean except ignored build/test artifacts.

## Decision gates

- **Integration:** parent review and full merged-tree suite; remove branch-local dossier as appropriate when integrated.
- **Activation:** independently verify each exact target harness, selected sources, native loader and existing safety behavior; no implied Hoster deployment.
- **Promotion:** separate owner authorization.

## Decision record

- 2026-11-05 — Narrowed feature to Codex/OpenCode instead of projecting a misleading OMP `spawns: []`; preserved existing local files as conflicts.
