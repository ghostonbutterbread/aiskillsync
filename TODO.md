# Future work

## External-source diff review before promotion

**Status:** planned

When an external bridge updates during sync, inspect the changed skill files
with deterministic indicators and an AI review before linking the new revision
into runtime destinations. Preserve the prior revision and report a held update
when review is uncertain or identifies a material capability change.

The current external-source feature intentionally retains normal auto-update
behavior: it records source provenance and keeps upstream checkouts organized,
but does not yet add an approval gate.