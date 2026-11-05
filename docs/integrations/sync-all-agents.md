# Sync all agent definitions integration dossier

- **Status:** feature; implementation awaiting independent review
- **Owner:** Hermes, task t_05035b5e
- **Branch / owning ref:** feat/sync-all-agents
- **Base commit:** 0fe1071a091e2417bfb31dbaaccbe5cb39763f91
- **Intended integration target:** beta
- **Latest immutable recovery checkpoint:** see branch tip
- **Feature implementation commits:** none yet

## Intent and scope

Make `sync all` synchronize configured agent definitions as well as skills while preserving profile isolation, collision safety, honest dry-run behavior and one repository materialization. Preserve independently filtered agent and skill commands. Hoster deployment and native consumer verification are separate gates. Avoid overwriting local agents or assuming native harness compatibility.

## Implemented contract and evidence

Two initial focused CLI tests were red at checkpoint e52c69b5cbb6f3a0ef4f97e12cbb4a08bdb44237. Implementation plans skills and agents together for unfiltered `sync all`, collects agent-only profile sources into the same materialization, checks agent conflicts before destination changes, and preserves focused skill and standalone agent commands. Tests cover mixed and agent-only profiles, legacy configuration, profile isolation, conflict, dry-run and idempotence. `python3 -m unittest tests.test_agents` passed 24; `python3 -m unittest discover -s tests` passed 139. Full filesystem apply is not transactional; partial failure is reported. Independent review has not yet occurred.

## Blockers / deferred tests

- Extend and independently review profile-only destinations, multiple profiles, filters, conflict handling, race conditions, and repository materialization before integration.
- Independently review and run full suite; reconcile beta before integration.
- Hoster activation requires compatibility inventory, existing-file comparison, a reviewed conflict-safe plan and exact native consumer discovery. Bunny dynamic model routing must be verified separately.

## Resume / gates

Resume with independent review of cli.py `cmd_sync`, `_resolve_sync_requests`, and agents.py `build_agent_sync_plan`, followed by focused fixes and tests. Integration target is beta; no main promotion. Do not claim Hoster activation without runtime verification. Dossier is temporary and must not remain on beta.
