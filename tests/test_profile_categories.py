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

    def test_profile_group_fans_out_selected_sources_to_every_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "general"
            for category, name in (("security", "xss"), ("career", "job-search")):
                skill = bridge / "skills" / category / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(name, encoding="utf-8")
            destinations = {name: base / name for name in ("claude", "codex", "hermes-security")}
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: general
    path: {bridge}
    skill_layout: categorized
ai_skill_paths:
  claude: {destinations['claude']}
  codex: {destinations['codex']}
  hermes-security: {destinations['hermes-security']}
sync:
  pull_before_sync: false
  clone_if_missing: false
profile_sets:
  security:
    destinations: [claude, codex, hermes-security]
    sources:
      - bridge: general
        include_categories: [security]
""",
                encoding="utf-8",
            )

            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = main(["--config", str(config), "sync", "all"])
            profile_stdout, profile_stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(profile_stdout), contextlib.redirect_stderr(profile_stderr):
                profile_result = main(["--config", str(config), "sync", "--profile", "security", "--dry-run"])
            linked = {
                destination: {name: (root / name).is_symlink() for name in ("xss", "job-search")}
                for destination, root in destinations.items()
            }

        self.assertEqual(result, 0, stderr.getvalue())
        self.assertEqual(profile_result, 0, profile_stderr.getvalue())
        self.assertIn("Destinations: claude, codex, hermes-security", profile_stdout.getvalue())
        self.assertEqual(
            linked,
            {name: {"xss": True, "job-search": False} for name in destinations},
        )

    def test_legacy_singular_profile_destination_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = config_from_mapping(
                {
                    "bridges": [{"name": "general", "path": str(base / "general")}],
                    "ai_skill_paths": {"codex": str(base / "codex")},
                    "profile_sets": {
                        "legacy": {"destination": "codex", "sources": [{"bridge": "general"}]}
                    },
                },
                base / "config.yaml",
            )

        self.assertEqual(config.profile_sets[0].destinations, ("codex",))
        self.assertIn("destinations:", config_to_text(config))
        self.assertNotIn("    destination:", config_to_text(config))

    def test_profile_groups_reject_duplicate_destination_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with self.assertRaisesRegex(ConfigError, "assigned by more than one profile set: codex"):
                config_from_mapping(
                    {
                        "bridges": [{"name": "general", "path": str(base / "general")}],
                        "ai_skill_paths": {"codex": str(base / "codex"), "claude": str(base / "claude")},
                        "profile_sets": {
                            "security": {
                                "destinations": ["codex", "claude"],
                                "sources": [{"bridge": "general"}],
                            },
                            "coding": {"destination": "codex", "sources": [{"bridge": "general"}]},
                        },
                    },
                    base / "config.yaml",
                )

    def test_profile_groups_validate_plural_destination_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            base_config = {
                "bridges": [{"name": "general", "path": str(base / "general")}],
                "ai_skill_paths": {"codex": str(base / "codex")},
            }
            invalid_profiles = (
                (
                    {"destination": "codex", "destinations": ["codex"], "sources": [{"bridge": "general"}]},
                    "cannot supply both",
                ),
                ({"sources": [{"bridge": "general"}]}, "must supply destination or destinations"),
                ({"destinations": ["missing"], "sources": [{"bridge": "general"}]}, "unknown destination: missing"),
                ({"destinations": ["codex", "codex"], "sources": [{"bridge": "general"}]}, "duplicate destination names"),
            )

            for profile, message in invalid_profiles:
                with self.subTest(message=message), self.assertRaisesRegex(ConfigError, message):
                    config_from_mapping(
                        {**base_config, "profile_sets": {"security": profile}},
                        base / "config.yaml",
                    )

    def test_omitted_categories_select_all_then_exclude_subtree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "general"
            for category, name in (("security", "xss"), ("career", "job-search")):
                skill = bridge / "skills" / category / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(name, encoding="utf-8")
            config = config_from_mapping(
                {
                    "bridges": [{"name": "general", "path": str(bridge), "skill_layout": "categorized"}],
                    "ai_skill_paths": {"codex": str(base / "codex")},
                    "profile_sets": {
                        "security": {
                            "destinations": ["codex"],
                            "sources": [{"bridge": "general", "exclude_categories": ["career"]}],
                        }
                    },
                },
                base / "config.yaml",
            )

            plan = build_sync_plan(
                config,
                ("general",),
                ("codex",),
                dry_run=True,
                exclude_category_filters={"general": config.profile_sets[0].sources[0].exclude_categories},
            )

        self.assertFalse(plan.errors)
        self.assertEqual([action.skill.name for action in plan.actions], ["xss"])

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

    def test_sync_profile_group_fans_out_to_all_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "security"
            skill = bridge / "skills" / "xss"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("xss", encoding="utf-8")
            destinations = {name: base / name for name in ("claude", "codex")}
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: security
    path: {bridge}
ai_skill_paths:
  claude: {destinations['claude']}
  codex: {destinations['codex']}
sync:
  pull_before_sync: false
  clone_if_missing: false
profile_sets:
  security:
    destinations: [claude, codex]
    sources:
      - bridge: security
""",
                encoding="utf-8",
            )

            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = main(["--config", str(config), "sync", "--profile", "security"])
            links = {name: (root / "xss").is_symlink() for name, root in destinations.items()}

        self.assertEqual(result, 0, stderr.getvalue())
        self.assertEqual(links, {"claude": True, "codex": True})

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

    def test_sync_profile_prunes_removed_source_only_after_it_was_managed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            sources = {name: base / name for name in ("source-a", "source-b")}
            for name, root in sources.items():
                skill = root / "skills" / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(name, encoding="utf-8")
            destination = base / "profile"
            config = base / "config.yaml"

            def write_config(source_names: tuple[str, ...]) -> None:
                source_entries = "\n".join(
                    f"      - bridge: {name}" for name in source_names
                )
                bridge_entries = "\n".join(
                    f"  - name: {name}\n    path: {root}" for name, root in sources.items()
                )
                config.write_text(
                    f"""bridges:
{bridge_entries}
ai_skill_paths:
  profile: {destination}
sync:
  pull_before_sync: false
  clone_if_missing: false
profile_sets:
  focused:
    destination: profile
    sources:
{source_entries}
""",
                    encoding="utf-8",
                )

            write_config(("source-a", "source-b"))
            initial_stdout, initial_stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(initial_stdout), contextlib.redirect_stderr(initial_stderr):
                initial_result = main(["--config", str(config), "sync", "--profile", "focused"])
            external_link = destination / "external"
            external_link.parent.mkdir(parents=True, exist_ok=True)
            external_link.symlink_to(sources["source-b"] / "skills" / "source-b", target_is_directory=True)
            write_config(("source-a",))
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = main(["--config", str(config), "sync", "--profile", "focused"])
            retained = (destination / "source-a").is_symlink()
            removed = not (destination / "source-b").exists() and not (destination / "source-b").is_symlink()
            external_retained = external_link.is_symlink()

        self.assertEqual(initial_result, 0, initial_stderr.getvalue())
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertTrue(retained)
        self.assertTrue(removed)
        self.assertTrue(external_retained)

    def test_focused_profile_skill_sync_does_not_prune_other_managed_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "source"
            for name in ("alpha", "beta"):
                skill = bridge / "skills" / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(name, encoding="utf-8")
            destination = base / "profile"
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: source
    path: {bridge}
ai_skill_paths:
  profile: {destination}
sync:
  pull_before_sync: false
  clone_if_missing: false
profile_sets:
  focused:
    destination: profile
    sources:
      - bridge: source
""",
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                initial_result = main(["--config", str(config), "sync", "--profile", "focused"])
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                focused_result = main(
                    ["--config", str(config), "sync", "--profile", "focused", "--skill", "alpha"]
                )

            alpha_retained = (destination / "alpha").is_symlink()
            beta_retained = (destination / "beta").is_symlink()

        self.assertEqual(initial_result, 0)
        self.assertEqual(focused_result, 0, stderr.getvalue())
        self.assertTrue(alpha_retained)
        self.assertTrue(beta_retained)
        self.assertNotIn("PRUNE", stdout.getvalue())

    def test_sync_all_prunes_removed_profile_source_without_touching_another_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            sources = {name: base / name for name in ("a", "b", "other")}
            for name, root in sources.items():
                skill = root / "skills" / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(name, encoding="utf-8")
            destinations = {"focused": base / "focused", "other": base / "other-destination"}
            config = base / "config.yaml"

            def write_config(focused_sources: tuple[str, ...]) -> None:
                bridges = "\n".join(f"  - name: {name}\n    path: {path}" for name, path in sources.items())
                selected = "\n".join(f"      - bridge: {name}" for name in focused_sources)
                config.write_text(
                    f"""bridges:
{bridges}
ai_skill_paths:
  focused: {destinations['focused']}
  other: {destinations['other']}
sync:
  pull_before_sync: false
  clone_if_missing: false
profile_sets:
  focused:
    destination: focused
    sources:
{selected}
  other:
    destination: other
    sources:
      - bridge: other
""",
                    encoding="utf-8",
                )

            write_config(("a", "b"))
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                initial_result = main(["--config", str(config), "sync", "all"])
            write_config(("a",))
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = main(["--config", str(config), "sync", "all"])
            focused_b_removed = not (destinations["focused"] / "b").exists() and not (destinations["focused"] / "b").is_symlink()
            other_retained = (destinations["other"] / "other").is_symlink()

        self.assertEqual(initial_result, 0)
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertTrue(focused_b_removed)
        self.assertTrue(other_retained)

    def test_dormant_profile_group_allows_no_destinations_and_sync_all_skips_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "general"
            skill = bridge / "skills" / "shared"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("shared", encoding="utf-8")
            destination = base / "security-destination"
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: general
    path: {bridge}
ai_skill_paths:
  security: {destination}
sync:
  pull_before_sync: false
  clone_if_missing: false
profile_sets:
  security:
    destinations: [security]
    sources:
      - bridge: general
  coding:
    destinations: []
    sources:
      - bridge: general
""",
                encoding="utf-8",
            )
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = main(["--config", str(config), "sync", "all"])

            self.assertEqual(result, 0, stderr.getvalue())
            self.assertTrue((destination / "shared").is_symlink())
    def test_profile_groups_reject_destination_names_with_same_canonical_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "shared"
            root.mkdir()
            alias = base / "alias"
            alias.symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ConfigError, "share canonical root path"):
                config_from_mapping(
                    {
                        "bridges": [{"name": "source", "path": str(base / "source")}],
                        "ai_skill_paths": {"first": str(root), "second": str(alias)},
                        "profile_sets": {
                            "one": {"destination": "first", "sources": [{"bridge": "source"}]},
                            "two": {"destination": "second", "sources": [{"bridge": "source"}]},
                        },
                    },
                    base / "config.yaml",
                )


if __name__ == "__main__":
    unittest.main()
