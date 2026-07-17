# aiskillsync contributor notes

## Versioning and pipx releases

`aiskillsync` is consumed as a pipx-installed CLI. **Every commit that changes
this repository must bump the patch version by one** in both places:

- `pyproject.toml` → `[project].version`
- `aiskillsync/__init__.py` → `__version__`

Keep the values identical. Current versioning is simple patch-only increments:
`0.1.0` → `0.1.1` → `0.1.2`.

Before committing, verify both `aiskillsync --version` and package metadata
report the new version. After pushing, installations created from the GitHub
source can update with:

```bash
pipx upgrade aiskillsync
```

For this workflow, install from the repository source once rather than from a
local checkout:

```bash
pipx install git+https://github.com/ghostonbutterbread/aiskillsync.git
```

Do not use `pipx --force` as the normal update mechanism; a new version and
`pipx upgrade` are the expected release path.

## Change discipline

- Keep CLI/config changes backward compatible unless a migration is documented.
- Add focused tests for every changed behavior and run the full unittest suite.
- Do not edit a user's `~/.config/aiskillsync/config.yaml` while developing the tool.
