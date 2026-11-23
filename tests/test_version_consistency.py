from __future__ import annotations

from pathlib import Path
import re
import unittest

from aiskillsync import __version__

ROOT = Path(__file__).resolve().parents[1]


class VersionConsistencyTests(unittest.TestCase):
    """pyproject and __init__ must agree.

    They drifted once: a release bumped pyproject only, so the wheel reported a
    newer version in its metadata while `aiskillsync --version` kept printing
    the older one. That makes it impossible to tell from the CLI whether a fix
    is actually installed.
    """

    def test_pyproject_version_matches_dunder_version(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version = "([^"]+)"', text, re.M)
        self.assertIsNotNone(match, "pyproject.toml has no top-level version")
        self.assertEqual(
            match.group(1),
            __version__,
            "pyproject.toml version and aiskillsync.__version__ disagree",
        )


if __name__ == "__main__":
    unittest.main()
