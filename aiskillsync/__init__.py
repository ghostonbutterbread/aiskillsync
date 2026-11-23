"""Global AI skill synchronization tooling."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _installed_version
from pathlib import Path
import re


def _version_from_pyproject() -> str | None:
    """Read the version straight out of pyproject when running from a checkout."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    return match.group(1) if match else None


def _resolve_version() -> str:
    """pyproject.toml is the single source of truth.

    This used to be a hardcoded string, which drifted: a release bumped
    pyproject only, so the wheel advertised the new version while
    `aiskillsync --version` kept printing the old one, making the CLI useless
    for checking whether a fix was installed. Deriving it removes the second
    place to forget.
    """
    try:
        return _installed_version("aiskillsync")
    except PackageNotFoundError:
        return _version_from_pyproject() or "0+unknown"


__version__ = _resolve_version()
