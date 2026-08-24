from __future__ import annotations

from pathlib import Path
import contextlib
import io
import tempfile
import unittest

from aiskillsync.cli import main
from aiskillsync.config import ConfigError, config_from_mapping, config_to_text, parse_simple_yaml
from aiskillsync.discovery import discover_bridges
from aiskillsync.sync import build_sync_plan


class ProfileCategoryTests(unittest.TestCase):
    def test_categorized_bridge_discovers_nested_skill_with_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "general"
            skill = root / "skills" / "career" / "job-search"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("job", encoding="utf-8")
            config = config_from_mapping(
                {
                    "bridges": [
                        {
                            "name": "general",
                            "path": str(root),
                            "skills_path": "skills",
                            "skill_layout": "categorized",
                        }
                    ],
                    "ai_skill_paths": {"job": str(Path(tmp) / "dest")},
                    "profile_sets": {
                        "jobsearch": {
                            "destination": "job",
                            "sources": [{"bridge": "general", "include_categories": ["career"]}],
                        }
                    },
                },
                Path(tmp) / "config.yaml",
            )
            discovery = discover_bridges(config)[0]

            self.assertEqual(discovery.skills[0].name, "job-search")
            self.assertEqual(discovery.skills[0].category, "career")
            self.assertEqual(config.profile_sets[0].sources[0].include_categories, ("career",))

    def test_flat_bridge_keeps_direct_skill_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "flat"
            skill = root / "skills" / "xss"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("xss", encoding="utf-8")
            config = config_from_mapping(
                {"bridges": [{"name": "flat", "path": str(root), "skills_path": "skills"}]},
                Path(tmp) / "config.yaml",
            )
            discovered = discover_bridges(config)[0].skills
            self.assertEqual([(item.name, item.category) for item in discovered], [("xss", "")])

    def test_config_serialization_preserves_layout_and_profile_sets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = config_from_mapping(
                {
                    "bridges": [{"name": "general", "path": str(base / "general"), "skill_layout": "categorized"}],
                    "ai_skill_paths": {"job": str(base / "job")},
                    "profile_sets": {
                        "jobsearch": {
                            "destination": "job",
                            "sources": [{"bridge": "general", "include_categories": ["career"]}],
                        }
                    },
                },
                base / "config.yaml",
            )
            restored = config_from_mapping(
                parse_simple_yaml(config_to_text(config)),
                base / "restored.yaml",
            )

        self.assertEqual(restored.bridges[0].skill_layout, "categorized")
        self.assertEqual(restored.profile_sets, config.profile_sets)

    def test_include_categories_all_is_a_broad_categorized_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "general"
            for category, name in (("career", "job-search"), ("core", "safe-fetch")):
                skill = bridge / "skills" / category / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(name, encoding="utf-8")
            config = config_from_mapping(
                {
                    "bridges": [{"name": "general", "path": str(bridge), "skill_layout": "categorized"}],
                    "ai_skill_paths": {"codex": str(base / "codex")},
                    "profile_sets": {
                        "codex": {
                            "destination": "codex",
                            "sources": [{"bridge": "general", "include_categories": "all", "exclude_categories": ["career"]}],
                        }
                    },
                },
                base / "config.yaml",
            )

            config.path.write_text(config_to_text(config), encoding="utf-8")
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                exit_code = main(["--config", str(config.path), "sync", "all", "--dry-run"])
            serialized = config_to_text(config)

        self.assertEqual(exit_code, 0, errors.getvalue())
        self.assertIn("safe-fetch", output.getvalue())
        self.assertNotIn("job-search", output.getvalue())
        self.assertIn("include_categories: all", serialized)

    def test_config_serialization_preserves_excluded_categories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = config_from_mapping(
                {
                    "bridges": [{"name": "general", "path": str(base / "general"), "skill_layout": "categorized"}],
                    "ai_skill_paths": {"codex": str(base / "codex")},
                    "profile_sets": {
                        "codex": {
                            "destination": "codex",
                            "sources": [{"bridge": "general", "exclude_categories": ["career"]}],
                        }
                    },
                },
                base / "config.yaml",
            )
            restored = config_from_mapping(parse_simple_yaml(config_to_text(config)), base / "restored.yaml")

        self.assertEqual(restored.profile_sets[0].sources[0].exclude_categories, ("career",))

    def test_profile_sets_require_every_destination_to_be_assigned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with self.assertRaisesRegex(ConfigError, "every ai_skill_paths destination must be assigned: claude"):
                config_from_mapping(
                    {
                        "bridges": [{"name": "general", "path": str(base / "general")}],
                        "ai_skill_paths": {"codex": str(base / "codex"), "claude": str(base / "claude")},
                        "profile_sets": {"codex": {"destination": "codex", "sources": [{"bridge": "general"}]}},
                    },
                    base / "config.yaml",
                )

    def test_category_aware_planner_selects_only_requested_subtree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "general"
            for category, name in (("career", "job-search"), ("security", "xss")):
                skill = bridge / "skills" / category / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(name, encoding="utf-8")
            config = config_from_mapping(
                {
                    "bridges": [{"name": "general", "path": str(bridge), "skill_layout": "categorized"}],
                    "ai_skill_paths": {"job": str(base / "job")},
                },
                base / "config.yaml",
            )

            plan = build_sync_plan(
                config,
                ("general",),
                ("job",),
                dry_run=True,
                category_filters={"general": ("career",)},
            )

        self.assertFalse(plan.errors)
        self.assertEqual([action.skill.name for action in plan.actions], ["job-search"])

    def test_category_aware_planner_rejects_unknown_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "general"
            skill = bridge / "skills" / "career" / "job-search"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("job", encoding="utf-8")
            config = config_from_mapping(
                {
                    "bridges": [{"name": "general", "path": str(bridge), "skill_layout": "categorized"}],
                    "ai_skill_paths": {"job": str(base / "job")},
                },
                base / "config.yaml",
            )

            plan = build_sync_plan(
                config,
                ("general",),
                ("job",),
                dry_run=True,
                category_filters={"general": ("missing",)},
            )

        self.assertIn("unknown category 'missing' for categorized bridge general", plan.errors)

    def test_category_aware_planner_rejects_category_filter_for_flat_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "flat"
            skill = bridge / "skills" / "xss"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("xss", encoding="utf-8")
            config = config_from_mapping(
                {
                    "bridges": [{"name": "flat", "path": str(bridge)}],
                    "ai_skill_paths": {"job": str(base / "job")},
                },
                base / "config.yaml",
            )

            plan = build_sync_plan(
                config,
                ("flat",),
                ("job",),
                dry_run=True,
                category_filters={"flat": ("career",)},
            )

        self.assertIn("categories can only be selected from categorized bridge flat", plan.errors)

    def test_category_aware_planner_rejects_all_filter_for_flat_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "flat"
            skill = bridge / "skills" / "xss"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("xss", encoding="utf-8")
            config = config_from_mapping(
                {"bridges": [{"name": "flat", "path": str(bridge)}], "ai_skill_paths": {"job": str(base / "job")}},
                base / "config.yaml",
            )

            plan = build_sync_plan(
                config,
                ("flat",),
                ("job",),
                dry_run=True,
                category_filters={"flat": ("all",)},
            )

        self.assertIn("categories can only be selected from categorized bridge flat", plan.errors)

    def test_category_aware_planner_excludes_requested_subtree_after_inclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "general"
            for category, name in (("career", "job-search"), ("career/private", "resume"), ("security", "xss")):
                skill = bridge / "skills" / category / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(name, encoding="utf-8")
            config = config_from_mapping(
                {
                    "bridges": [{"name": "general", "path": str(bridge), "skill_layout": "categorized"}],
                    "ai_skill_paths": {"codex": str(base / "codex")},
                },
                base / "config.yaml",
            )

            plan = build_sync_plan(
                config,
                ("general",),
                ("codex",),
                dry_run=True,
                exclude_category_filters={"general": ("career",)},
            )

        self.assertFalse(plan.errors)
        self.assertEqual([action.skill.name for action in plan.actions], ["xss"])

    def test_category_aware_planner_rejects_category_exclusion_for_flat_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "flat"
            skill = bridge / "skills" / "xss"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("xss", encoding="utf-8")
            config = config_from_mapping(
                {"bridges": [{"name": "flat", "path": str(bridge)}], "ai_skill_paths": {"codex": str(base / "codex")}},
                base / "config.yaml",
            )

            plan = build_sync_plan(
                config,
                ("flat",),
                ("codex",),
                dry_run=True,
                exclude_category_filters={"flat": ("career",)},
            )

        self.assertIn("categories can only be selected from categorized bridge flat", plan.errors)

    def test_sync_profile_uses_its_named_destination_and_categories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "general"
            for category, name in (("career", "job-search"), ("security", "xss")):
                skill = bridge / "skills" / category / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(name, encoding="utf-8")
            profile_destination = base / "profile"
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: general
    path: {bridge}
    skills_path: skills
    skill_layout: categorized
ai_skill_paths:
  job: {profile_destination}
sync:
  pull_before_sync: false
  clone_if_missing: false
profile_sets:
  jobsearch:
    destination: job
    sources:
      - bridge: general
        include_categories:
          - career
""",
                encoding="utf-8",
            )
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = main(["--config", str(config), "sync", "--profile", "jobsearch"])

            job_link = profile_destination / "job-search"
            xss_link = profile_destination / "xss"
            job_is_symlink = job_link.is_symlink()
            job_target = job_link.resolve() if job_is_symlink else None
            xss_exists = xss_link.exists()

        self.assertEqual(result, 0, stderr.getvalue())
        self.assertTrue(job_is_symlink)
        self.assertEqual(job_target, bridge / "skills" / "career" / "job-search")
        self.assertFalse(xss_exists)

    def test_sync_all_projects_each_profile_without_broadcasting_categories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "general"
            for category, name in (("career", "job-search"), ("security", "xss")):
                skill = bridge / "skills" / category / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(name, encoding="utf-8")
            destinations = {name: base / name for name in ("jobsearch", "claude", "codex")}
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: general
    path: {bridge}
    skills_path: skills
    skill_layout: categorized
ai_skill_paths:
  jobsearch: {destinations['jobsearch']}
  claude: {destinations['claude']}
  codex: {destinations['codex']}
sync:
  pull_before_sync: false
  clone_if_missing: false
profile_sets:
  jobsearch:
    destination: jobsearch
    sources:
      - bridge: general
        include_categories:
          - career
  claude:
    destination: claude
    sources:
      - bridge: general
        exclude_categories:
          - career
  codex:
    destination: codex
    sources:
      - bridge: general
        exclude_categories:
          - career
""",
                encoding="utf-8",
            )

            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = main(["--config", str(config), "sync", "all"])
            linked = {
                destination: {name: (root / name).is_symlink() for name in ("job-search", "xss")}
                for destination, root in destinations.items()
            }

        self.assertEqual(result, 0, stderr.getvalue())
        self.assertEqual(linked["jobsearch"], {"job-search": True, "xss": False})
        self.assertEqual(linked["claude"], {"job-search": False, "xss": True})
        self.assertEqual(linked["codex"], {"job-search": False, "xss": True})


if __name__ == "__main__":
    unittest.main()
