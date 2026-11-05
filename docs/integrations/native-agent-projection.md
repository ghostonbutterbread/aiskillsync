# Native agent projection integration dossier

- **Status:** review repairs; integration blocked on parent base-lane fix, independent review, and deferred runtime evidence
- **Owner:** delegated native-agent-projection agent
- **Branch:** `feat/native-agent-projection`
- **Worktree:** `/home/ryushe/worktrees/aiskillsync-native-agent-projection`
- **Base commit:** `d3e254d5f61a8ecad61964b590092090c2e7ecc6` (`feat/sync-all-agents`)
- **Intended integration target:** `feat/sync-all-agents` (parent-owned; not beta directly)
- **Last updated:** 2026-11-05
- **Owning feature branch/ref:** `feat/native-agent-projection`
- **Latest immutable recovery checkpoint:** `5febec66e1ca7f7945840a47ac7562b1f176000e`; review repairs in the following commit at the feature branch tip
- **Feature implementation commits:** `5febec66e1ca7f7945840a47ac7562b1f176000e` and its review-repair successor (`git log feat/sync-all-agents..feat/native-agent-projection`)
- **Canonical references:** OpenAI Codex subagents documentation, OpenCode agents documentation, OMP `packages/coding-agent/src/discovery/helpers.ts` and `src/task/spawn-policy.ts` upstream; see README links.

## Intent and contract

Add explicitly configured, source-prompt-preserving generated files for Codex standalone agent TOML and OpenCode subagent Markdown. Models are not pinned. OpenCode output sets `permission.task: deny`; Codex inherits the parent's permissions and relies on source instructions / runtime limits rather than claiming equivalent enforcement. Native conversion rejects source fields besides name and description. Source/destination root overlap blocks. New files use no-clobber creation; existing changed native files always block and require the operator-owned stop-consumer, move-to-backup, recreate/restore workflow in README. Identifiable stale, malformed, renamed and changed-format definitions block rather than disappearing or remaining silently active; raw links with selected source roots are checked too. Multi-file failure reports completed paths and backup location, with manual recovery; it is not a transaction. Different-source/lane ownership requires explicit reconciliation.

## Evidence and review

- Focused: `OPENCODE_TEST_BIN=<isolated opencode 1.19.5 binary> python3 -m unittest discover -s tests -p test_agents.py -v`; 38 passed including actual isolated OpenCode `agent list` loader discovery. Deterministic tests inject concurrent destination creation, concurrent edits, second-write failure and adoption failure; source removal/malformation, rename, format change, overlap, and backup receipts are covered. No live profile, credentials, model, or target traffic.
- Full: `OPENCODE_TEST_BIN=<same> python3 -m unittest discover -s tests -v`; 153 passed, with HOME/XDG paths inside private scratch. Re-run after any merge. A first run exposed a test comparing filesystem atime; the dry-run check now compares durable metadata rather than read-induced access times.
- Codex: stdlib `tomllib` parses generated TOML and checks instructions/model omission; native Codex loader/spawn not exercised. `python3 -m aiskillsync --version` and built wheel metadata report 0.1.29.
- Independent review identified native replace race, partial writes, overlap, stale definitions, missing backup receipt and inherited base-lane issues. This successor addresses the native issues with fail-closed/manual recovery; a fresh independent review remains required. Parent owns integration decision. No push, merge or activation.

## Blockers and deferred work

1. **OMP safety and per-invocation routing:** upstream `parseAgentFields` calls `parseArrayOrCSV([])` which yields `undefined`; `resolveSpawnPolicy(undefined)` is unrestricted. Thus existing `spawns: []` cannot be assumed effective, and projecting it would make a false safety claim. Exact installed Hoster OMP revision and behavior are unverified. Need an upstream-fixed or otherwise tested no-delegation contract, plus reconciliation of local prompt/tool/skill-preload fields and a version-matched isolated native loader + spawn-policy test before adding an OMP format. Do not overwrite local OMP definitions; legacy raw-link adoption remains separately dangerous and parent must evaluate it.
2. **Codex loader:** current OpenAI docs specify standalone TOML auto-discovery, but installed host version and native invocation were not verified. In a disposable credential-free `CODEX_HOME`, use the exact intended Codex version and its actual discovery interface to confirm role name, instructions and inherited settings; if it only supports config-registered roles, registration must be separately designed and tested. A `tomllib` parse does not establish discovery.
3. **OpenCode semantics:** loader discovery is verified in fixture only; exact target version, child prompt/permission enforcement and model routing per spawn are not verified. Use a disposable fixture and non-network test provider/loader API before activation. Per-call model choice is runtime/version dependent; unpinned is not equivalent to per-call support.
4. **Pi / Claude:** no Pi extension contract was verified, and Claude remains legacy raw Markdown only, not an adapted projection; do not report native compatibility for a path alone. Review exact extension version and Claude loader before any claimed end-to-end coverage.
5. **Activation:** no Hoster writes or runtime configuration changes are authorized by this branch. Parent must inspect same-name definitions and migrate their distinct bodies, preloads, and safety constraints explicitly; backups alone do not reconcile them. Preserve unresolved files and report conflicts.
6. **Base lane blocker:** parent is separately fixing switch unexpectedly syncing/adopting agent files and applying `effective_config` twice. Do not integrate until that repair is propagated to the parent feature and the merged native candidate is re-reviewed and tested; this branch deliberately does not duplicate it.
7. **Recoverability limitations:** native refresh requires manual retirement, not automatic `sync all` updates. Partial writes are explicitly reported but not rolled back; abrupt termination can omit receipts, and skill, profile, and agent plans are not one transaction. No-clobber creates protect concurrent destination creation, not arbitrary external writers operating afterward. Marker removal/corruption makes retired files unidentifiable without a manifest; raw links to a removed source root likewise cannot be proven owned. Unmatched native markers from a different profile sharing a root conservatively block. A durable manifest/transaction would need a separate design and race analysis; never treat these cases as silently safe.

## Interruption / resume handoff

- **Owning feature branch/ref:** `feat/native-agent-projection`
- **Latest immutable recovery checkpoint:** `5febec66e1ca7f7945840a47ac7562b1f176000e` plus its review-repair successor at `git rev-parse feat/native-agent-projection`.
- **Exact resume point:** parent propagates its base-lane fix into `feat/sync-all-agents`, reconciles this successor, independently reviews the combined diff and reruns tests on the merged candidate; resolve deferred provider checks before activation.
- **Working-tree state at handoff:** expected clean except ignored build/test artifacts.

## Decision gates

- **Integration:** parent review and full merged-tree suite; remove branch-local dossier as appropriate when integrated.
- **Activation:** independently verify each exact target harness, selected sources, native loader and existing safety behavior; no implied Hoster deployment.
- **Promotion:** separate owner authorization.

## Decision record

- 2026-11-05 — Narrowed feature to Codex/OpenCode instead of projecting a misleading OMP `spawns: []`; preserved existing local files as conflicts.
