# Portable lane context

## Status

Implementation in progress on `feat/portable-lane-context`.

## Goal

After a successful aiskillsync lane switch, publish a host-local runtime receipt
that maps each logical lane group to its selected checkout, configured branch,
and Git revision. This lets optional cross-repository integrations use the
selected lane without embedding host checkout paths in shared skills or code.

## Boundaries

- Aiskillsync owns the generated receipt at `active-stack.json` next to its local
  configuration.
- The receipt is not shared configuration and is not committed by consumers.
- Applications remain standalone; consuming the receipt is only for a deliberate
  optional integration.
- This change does not activate a lane, modify the user's aiskillsync config, or
  change the installed CLI.

## Base and target

- Feature branch: `feat/portable-lane-context`
- Base: `main` at `8dea009`
- Intended integration target: `main`
- Companion BBH branch: `fix/portable-lane-context`, target `beta`.

## Evidence

- Focused test: `python3 -m unittest tests.test_lanes.LaneCliTests.test_switch_writes_resolved_runtime_context -v`
- Full suite: `python3 -m unittest discover -s tests -v`
- Isolated BBH stable/beta switch: temporary config verified both the projected
  `ssrf` skill symlink and the selected root/revision in `active-stack.json`.

## Activation boundary

After review and integration, release the new aiskillsync version, install or
upgrade it on the host, then run the desired lane switch. Existing running agent
processes must be restarted or relaunched before they can consume a new runtime
context.
