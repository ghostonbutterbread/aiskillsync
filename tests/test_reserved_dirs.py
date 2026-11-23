from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from aiskillsync.config import config_from_mapping
from aiskillsync.discovery import discover_bridges


class ReservedDirectoryTests(unittest.TestCase):
    """Underscore-prefixed directories under the skills root are not skills.

    Repositories keep archives, retained history and run folders in `_`-prefixed
    directories. Scanning them reported a missing SKILL.md, and a single such
    report blocks an entire profile sync.
    """

    def test_flat_bridge_ignores_underscore_directory_without_skill_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "flat"
            (root / "skills" / "xss").mkdir(parents=True)
            (root / "skills" / "xss" / "SKILL.md").write_text("xss", encoding="utf-8")
            # an archive holding a retired skill one level down
            retired = root / "skills" / "_archive" / "security-reporting"
            retired.mkdir(parents=True)
            (retired / "SKILL.md").write_text("retired", encoding="utf-8")
            config = config_from_mapping(
                {"bridges": [{"name": "flat", "path": str(root), "skills_path": "skills"}]},
                Path(tmp) / "config.yaml",
            )
            discovery = discover_bridges(config)[0]

            self.assertEqual([item.name for item in discovery.skills], ["xss"])
            self.assertEqual(discovery.missing_skill_md, ())

    def test_categorized_bridge_ignores_underscore_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "policies"
            live = root / "skills" / "core" / "live-testing-policy"
            live.mkdir(parents=True)
            (live / "SKILL.md").write_text("live", encoding="utf-8")
            archived = root / "skills" / "_archive" / "http-error-live-policy"
            archived.mkdir(parents=True)
            (archived / "SKILL.md").write_text("alias", encoding="utf-8")
            config = config_from_mapping(
                {
                    "bridges": [
                        {
                            "name": "policies",
                            "path": str(root),
                            "skills_path": "skills",
                            "skill_layout": "categorized",
                        }
                    ]
                },
                Path(tmp) / "config.yaml",
            )
            discovery = discover_bridges(config)[0]

            # the archived alias must not be projected as a live skill
            self.assertEqual([item.name for item in discovery.skills], ["live-testing-policy"])
            self.assertEqual([item.category for item in discovery.skills], ["core"])
            self.assertEqual(discovery.missing_skill_md, ())

    def test_underscore_directory_at_the_skills_root_is_not_reported_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "flat"
            (root / "skills" / "xss").mkdir(parents=True)
            (root / "skills" / "xss" / "SKILL.md").write_text("xss", encoding="utf-8")
            (root / "skills" / "_batches").mkdir(parents=True)  # no SKILL.md anywhere
            config = config_from_mapping(
                {"bridges": [{"name": "flat", "path": str(root), "skills_path": "skills"}]},
                Path(tmp) / "config.yaml",
            )
            discovery = discover_bridges(config)[0]

            self.assertEqual(discovery.missing_skill_md, ())

    def test_ordinary_directory_without_skill_md_is_still_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "flat"
            (root / "skills" / "xss").mkdir(parents=True)
            (root / "skills" / "xss" / "SKILL.md").write_text("xss", encoding="utf-8")
            (root / "skills" / "broken").mkdir(parents=True)
            config = config_from_mapping(
                {"bridges": [{"name": "flat", "path": str(root), "skills_path": "skills"}]},
                Path(tmp) / "config.yaml",
            )
            discovery = discover_bridges(config)[0]

            self.assertEqual(
                [item.name for item in discovery.missing_skill_md], ["broken"]
            )


if __name__ == "__main__":
    unittest.main()
