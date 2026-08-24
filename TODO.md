# Future work

## External-source diff review before promotion

**Status:** planned

When an external bridge updates during sync, inspect the changed skill files
with deterministic indicators and an AI review before linking the new revision
into runtime destinations. Preserve the prior revision and report a held update
when review is uncertain or identifies a material capability change.

For a newly added external bridge, the review receipt must also record the
configured `skills_path`, verify that it corresponds to the intended
plugin/marketplace entry, and list every discovered `<skill>/SKILL.md` that will
be projected. A repository may export multiple sibling skills from the selected
root; do not recursively select an arbitrary `SKILL.md` elsewhere in the tree.

The current external-source feature intentionally retains normal auto-update
behavior: it records source provenance and keeps upstream checkouts organized,
but does not yet add an approval gate.