# Sync all agent definitions integration dossier

- **Status:** feature; initial red regression checkpoint
- **Owner:** Hermes, task t_05035b5e
- **Branch / owning ref:** feat/sync-all-agents
- **Base commit:** 0fe1071a091e2417bfb31dbaaccbe5cb39763f91
- **Intended integration target:** beta
- **Latest immutable recovery checkpoint:** see branch tip
- **Feature implementation commits:** none yet

## Intent and scope

Make `sync all` synchronize configured agent definitions as well as skills while preserving profile isolation, collision safety, honest dry-run behavior and one repository materialization. Preserve independently filtered agent and skill commands. Hoster deployment and native consumer verification are separate gates. Avoid overwriting local agents or assuming native harness compatibility.

## Implemented contract and evidence

Two focused CLI tests describe mixed skill/agent destinations, dry-run no mutation, idempotence and agent-conflict blocking. RED on base: `python3 -m unittest tests.test_agents.AgentSyncTests.test_sync_all_projects_skills_and_agents_to_mixed_profile_destinations tests.test_agents.AgentSyncTests.test_sync_all_agent_conflict_blocks_skill_and_agent_writes` fails both as expected; no implementation yet. A prior skills-only `sync all` expectation is deliberately updated for the user-requested semantic change.

## Blockers / deferred tests

- Implement `sync all` using agent and skill planners; cover profile-only destinations, legacy configs, multiple profiles, filters, conflict handling, and repository materialization before review.
- Independently review and run full suite; reconcile beta before integration.
- Hoster activation requires compatibility inventory, existing-file comparison, a reviewed conflict-safe plan and exact native consumer discovery. Bunny dynamic model routing must be verified separately.

## Resume / gates

Resume by reading cli.py `cmd_sync`, `_resolve_sync_requests`, and agents.py `build_agent_sync_plan`, then implement and rerun focused tests. Integration target is beta; no main promotion. Do not claim Hoster activation without runtime verification. Dossier is temporary and must not remain on beta.
