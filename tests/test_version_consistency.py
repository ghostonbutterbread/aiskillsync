from __future__ import annotations

from pathlib import Path
import contextlib
import io
import re
import unittest

from aiskillsync import __version__, _version_from_pyproject
from aiskillsync.cli import main

ROOT = Path(__file__).resolve().parents[1]


class VersionConsistencyTests(unittest.TestCase):
    """pyproject.toml is the only place the version is written.

    It used to be duplicated in aiskillsync/__init__.py and the two drifted: a
    release bumped pyproject only, so the wheel advertised the new version
    while `aiskillsync --version` printed the old one. A guard that merely
    compared the two would still have allowed the duplication, so these tests
    assert the duplication is gone.
    """

    def _pyproject_version(self) -> str:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        self.assertIsNotNone(match, "pyproject.toml has no top-level version")
        return match.group(1)

    def test_init_does_not_hardcode_a_version(self) -> None:
        source = (ROOT / "aiskillsync" / "__init__.py").read_text(encoding="utf-8")
        literals = re.findall(r'__version__\s*=\s*"[^"]+"', source)
        self.assertEqual(
            literals, [], "__version__ is assigned a literal; derive it instead"
        )

    def test_resolved_version_matches_pyproject(self) -> None:
        self.assertEqual(__version__, self._pyproject_version())

    def test_source_checkout_resolves_without_install_metadata(self) -> None:
        """A checkout with no installed distribution still reports the version."""
        self.assertEqual(_version_from_pyproject(), self._pyproject_version())

    def test_cli_reports_the_same_version(self) -> None:
        """--version is what a user checks to confirm a fix is installed."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(SystemExit):
                main(["--version"])
        self.assertEqual(buffer.getvalue().strip(), f"aiskillsync {self._pyproject_version()}")


if __name__ == "__main__":
    unittest.main()
