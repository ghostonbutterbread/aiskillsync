from __future__ import annotations

import os
import errno
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from aiskillsync.config import ConfigError, config_from_mapping, config_to_text, parse_simple_yaml
from aiskillsync.agents import build_agent_sync_plan, apply_agent_sync_plan


ROOT = Path(__file__).resolve().parents[1]


class AgentSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "bbh"
        self.agents = self.source / "agents"
        self.agents.mkdir(parents=True)
        self.destination = self.base / "claude" / "agents"
        self.config = self.base / "config.yaml"
        self.raw = {
            "bridges": [{"name": "bbh", "path": str(self.source), "agents_path": "agents"}],
            "ai_skill_paths": {"claude": str(self.base / "claude" / "skills")},
            "ai_agent_paths": {"claude": str(self.destination)},
            "sync": {"pull_before_sync": False, "clone_if_missing": False},
        }

    def write_config(self):
        # The initial fixture is literal YAML so an old serializer cannot hide fields.
        self.config.write_text(
            f"bridges:\n  - name: bbh\n    path: {self.source}\n    agents_path: agents\n"
            f"ai_skill_paths:\n  claude: {self.base / 'claude' / 'skills'}\n"
            f"ai_agent_paths:\n  claude: {self.destination}\n"
            "sync:\n  pull_before_sync: false\n  clone_if_missing: false\n",
            encoding="utf-8",
        )

    def agent(self, filename="reviewer.md", name="reviewer", description="Review changes"):
        path = self.agents / filename
        path.write_text(f"---\nname: {name}\ndescription: {description}\n---\nReview safely.\n", encoding="utf-8")
        return path

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "aiskillsync", "--config", str(self.config), *args],
            cwd=ROOT, env={**os.environ, "HOME": str(self.base / "home")},
            text=True, capture_output=True,
        )

    @unittest.skipUnless(importlib.util.find_spec("tomllib"), "tomllib requires Python 3.11+")
    def test_native_codex_projection_preserves_prompt_and_inherits_model(self):
        import tomllib
        source = self.agent()
        self.raw["agent_formats"] = {"claude": "codex"}
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        self.assertFalse(plan.has_blockers, plan.errors)
        apply_agent_sync_plan(plan)
        target = self.destination / "reviewer.toml"
        parsed = tomllib.loads(target.read_text())
        self.assertEqual(parsed["name"], "reviewer")
        self.assertEqual(parsed["description"], "Review changes")
        self.assertEqual(parsed["developer_instructions"], source.read_text().split("---\n", 2)[2])
        self.assertNotIn("model", parsed)
        again = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        self.assertEqual(again.actions[0].action, "skip")

    @unittest.skipUnless(importlib.util.find_spec("tomllib"), "tomllib requires Python 3.11+")
    def test_bunny_advertise_true_projects_instructions_for_both_formats(self):
        import tomllib
        for role in ("hunter", "recon", "verifier", "reporter"):
            source = self.agent(f"bunny-{role}.md", f"bunny-{role}", f"Bunny {role}")
            source.write_text(source.read_text().replace("description:", "advertise: true\ndescription:", 1))
        for provider in ("codex", "opencode"):
            with self.subTest(provider=provider):
                self.raw["ai_agent_paths"]["claude"] = str(self.base / provider)
                self.raw["agent_formats"] = {"claude": provider}
                plan = build_agent_sync_plan(config_from_mapping(self.raw, self.config), ("bbh",), ("claude",), dry_run=False)
                self.assertFalse(plan.has_blockers, plan.errors)
                apply_agent_sync_plan(plan)
                for role in ("hunter", "recon", "verifier", "reporter"):
                    target = self.base / provider / (f"bunny-{role}.toml" if provider == "codex" else f"bunny-{role}.md")
                    content = target.read_text()
                    if provider == "codex":
                        self.assertEqual(tomllib.loads(content)["developer_instructions"], "Review safely.\n")
                    else:
                        self.assertTrue(content.endswith("Review safely.\n"))
                        self.assertIn("mode: subagent\npermission:\n  task: deny", content)
        source.write_text(source.read_text().replace("advertise: true", "advertise: false"))
        plan = build_agent_sync_plan(config_from_mapping(self.raw, self.config), ("bbh",), ("claude",), dry_run=False)
        self.assertIn("cannot translate", " ".join(plan.errors))

    def test_crlf_managed_stale_projection_blocks_for_both_formats(self):
        source = self.agent()
        self.agent("other.md", "other")
        for provider in ("codex", "opencode"):
            with self.subTest(provider=provider):
                self.raw["ai_agent_paths"]["claude"] = str(self.base / provider)
                self.raw["agent_formats"] = {"claude": provider}
                config = config_from_mapping(self.raw, self.config)
                apply_agent_sync_plan(build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False))
                target = self.base / provider / ("reviewer.toml" if provider == "codex" else "reviewer.md")
                target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
                self.assertFalse(build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False).has_blockers)
                original = target.read_bytes()
                source.unlink()
                plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False, include_agents=("other",))
                self.assertIn("stale or invalid managed agent", " ".join(plan.errors))
                with self.assertRaises(ValueError):
                    apply_agent_sync_plan(plan)
                self.assertEqual(target.read_bytes(), original)
                self.agent()

    def test_source_change_at_link_is_reported_as_incomplete(self):
        source = self.agent()
        self.raw["agent_formats"] = {"claude": "opencode"}
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        real_link = os.link
        def race(src, dst, *args, **kwargs):
            source.write_text(source.read_text() + "new instruction\n")
            return real_link(src, dst, *args, **kwargs)
        with patch("aiskillsync.agents.os.link", side_effect=race):
            with self.assertRaisesRegex(ValueError, "incomplete.*reviewer.md.*source changed"):
                apply_agent_sync_plan(plan)
        self.assertFalse((self.destination / "reviewer.md").read_text().endswith("new instruction\n"))

    def test_adoption_concurrent_backup_creation_never_clobbers(self):
        self.agent()
        self.destination.mkdir(parents=True)
        target = self.destination / "reviewer.md"
        target.write_text("original")
        backup = self.base / "backups"
        plan = build_agent_sync_plan(config_from_mapping(self.raw, self.config), ("bbh",), ("claude",),
                                     dry_run=False, adopt=True, backup_root=backup)
        saved = backup / "agents" / "claude" / "reviewer.md"
        real_link = os.link
        def race(src, dst, *args, **kwargs):
            if Path(dst) == saved:
                saved.write_text("concurrent backup")
            return real_link(src, dst, *args, **kwargs)
        with patch("aiskillsync.agents.os.link", side_effect=race):
            with self.assertRaisesRegex(ValueError, "incomplete.*backup"):
                apply_agent_sync_plan(plan)
        self.assertEqual(saved.read_text(), "concurrent backup")
        self.assertEqual(target.read_text(), "original")

    def test_adoption_cross_filesystem_copy_is_exclusive(self):
        self.agent()
        self.destination.mkdir(parents=True)
        target = self.destination / "reviewer.md"
        target.write_text("original")
        backup = self.base / "backups"
        plan = build_agent_sync_plan(config_from_mapping(self.raw, self.config), ("bbh",), ("claude",),
                                     dry_run=False, adopt=True, backup_root=backup)
        with patch("aiskillsync.agents.os.link", side_effect=OSError(errno.EXDEV, "different filesystems")):
            apply_agent_sync_plan(plan)
        self.assertEqual((backup / "agents" / "claude" / "reviewer.md").read_text(), "original")
        self.assertTrue(target.is_symlink())

    def test_adoption_cross_filesystem_backup_collision_preserves_both(self):
        self.agent()
        self.destination.mkdir(parents=True)
        target = self.destination / "reviewer.md"
        target.write_text("original")
        backup = self.base / "backups"
        plan = build_agent_sync_plan(config_from_mapping(self.raw, self.config), ("bbh",), ("claude",),
                                     dry_run=False, adopt=True, backup_root=backup)
        saved = backup / "agents" / "claude" / "reviewer.md"
        def race(*args, **kwargs):
            saved.write_text("concurrent backup")
            raise OSError(errno.EXDEV, "different filesystems")
        with patch("aiskillsync.agents.os.link", side_effect=race):
            with self.assertRaisesRegex(ValueError, "incomplete.*backup"):
                apply_agent_sync_plan(plan)
        self.assertEqual(target.read_text(), "original")
        self.assertEqual(saved.read_text(), "concurrent backup")

    def test_adoption_directory_backup_preserves_contents(self):
        self.agent()
        target = self.destination / "reviewer.md"
        target.mkdir(parents=True)
        (target / "local.txt").write_text("original")
        backup = self.base / "backups"
        plan = build_agent_sync_plan(config_from_mapping(self.raw, self.config), ("bbh",), ("claude",),
                                     dry_run=False, adopt=True, backup_root=backup)
        apply_agent_sync_plan(plan)
        self.assertEqual((backup / "agents" / "claude" / "reviewer.md" / "local.txt").read_text(), "original")
        self.assertTrue(target.is_symlink())

    def test_native_updates_only_unmodified_managed_files_and_checks_source_races(self):
        source = self.agent()
        self.raw["agent_formats"] = {"claude": "opencode"}
        config = config_from_mapping(self.raw, self.config)
        first = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        source.write_text(source.read_text() + "changed")
        with self.assertRaisesRegex(ValueError, "source changed"):
            apply_agent_sync_plan(first)
        first = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        apply_agent_sync_plan(first)
        target = self.destination / "reviewer.md"
        self.assertIn("task: deny", target.read_text())
        self.assertNotIn("model:", target.read_text())
        source.write_text(source.read_text() + " update")
        update = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        self.assertEqual(update.actions[0].action, "conflict")
        self.assertIn("manual retirement", " ".join(update.errors))
        target.write_text(target.read_text() + " concurrent edit")
        with self.assertRaises(ValueError):
            apply_agent_sync_plan(update)
        self.assertTrue(target.read_text().endswith("concurrent edit"))
        backup = self.base / "reviewer-previous.md"
        target.rename(backup)
        apply_agent_sync_plan(build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False))
        self.assertTrue(backup.read_text().endswith("changed concurrent edit"))
        self.assertTrue(target.read_text().endswith("changed update"))
        target.write_text(target.read_text() + " local safety rule")
        conflict = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False, adopt=True,
                                         backup_root=self.base / "backups")
        self.assertTrue(conflict.has_blockers)
        self.assertEqual(conflict.actions[0].action, "conflict")
        with self.assertRaises(ValueError):
            apply_agent_sync_plan(conflict)
        self.assertTrue(target.read_text().endswith("local safety rule"))

    def test_native_rejects_untranslatable_source_fields_and_local_adoption(self):
        source = self.agent()
        source.write_text(source.read_text().replace("---\nReview", "spawns: []\n---\nReview"))
        self.raw["agent_formats"] = {"claude": "opencode"}
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        self.assertIn("cannot translate source frontmatter", " ".join(plan.errors))
        self.assertFalse(self.destination.exists())
        self.agent()
        self.destination.mkdir(parents=True)
        target = self.destination / "reviewer.md"
        target.write_text("---\nspawns: []\nautoloadSkills: [security]\n---\nlocal prompt")
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False, adopt=True,
                                     backup_root=self.base / "backups")
        self.assertTrue(plan.has_blockers)
        self.assertEqual(plan.actions[0].action, "conflict")
        self.assertIn("autoloadSkills", target.read_text())

    def test_native_concurrent_create_is_never_overwritten(self):
        self.agent()
        self.raw["agent_formats"] = {"claude": "opencode"}
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        real_link = os.link
        def race(src, dst):
            Path(dst).write_text("concurrent local edit")
            return real_link(src, dst)
        with patch("aiskillsync.agents.os.link", side_effect=race):
            with self.assertRaises(FileExistsError):
                apply_agent_sync_plan(plan)
        self.assertEqual((self.destination / "reviewer.md").read_text(), "concurrent local edit")

    def test_native_multi_file_failure_reports_recoverable_incomplete_state(self):
        self.agent()
        self.agent("second.md", "second")
        self.raw["agent_formats"] = {"claude": "opencode"}
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        real_link = os.link
        def fail_second(src, dst):
            if Path(dst).name == "second.md":
                raise OSError("injected failure")
            return real_link(src, dst)
        with patch("aiskillsync.agents.os.link", side_effect=fail_second):
            with self.assertRaisesRegex(ValueError, "incomplete.*reviewer.md.*injected failure"):
                apply_agent_sync_plan(plan)
        self.assertIn("task: deny", (self.destination / "reviewer.md").read_text())
        self.assertFalse((self.destination / "second.md").exists())
        self.assertFalse(list(self.destination.glob(".aiskillsync-agent-*")))
        retry = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        apply_agent_sync_plan(retry)
        self.assertTrue((self.destination / "second.md").exists())

    def test_native_stale_malformed_and_renamed_sources_block_without_mutation(self):
        source = self.agent()
        self.agent("other.md", "other")
        self.raw["agent_formats"] = {"claude": "opencode"}
        config = config_from_mapping(self.raw, self.config)
        apply_agent_sync_plan(build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False))
        target = self.destination / "reviewer.md"
        original = target.read_bytes()
        for broken in ("---\nname: reviewer\n---\n", "# Not an agent\n", None,
                       "---\nname: renamed\ndescription: New name\n---\n"):
            if broken is None:
                source.unlink()
            else:
                source.write_text(broken)
            plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False,
                                         include_agents=("other",))
            self.assertIn("stale or invalid managed agent", " ".join(plan.errors))
            with self.assertRaises(ValueError):
                apply_agent_sync_plan(plan)
            self.assertEqual(target.read_bytes(), original)
        self.assertTrue((self.destination / "other.md").exists())

    def test_agent_source_destination_overlap_is_blocked(self):
        self.agent()
        for root in (self.agents, self.agents / "nested", self.source):
            self.raw["ai_agent_paths"]["claude"] = str(root)
            self.raw["agent_formats"] = {"claude": "opencode"}
            plan = build_agent_sync_plan(config_from_mapping(self.raw, self.config), ("bbh",), ("claude",), dry_run=False)
            self.assertIn("overlap", " ".join(plan.errors))
            with self.assertRaises(ValueError):
                apply_agent_sync_plan(plan)

    def test_stale_raw_agent_link_blocks(self):
        source = self.agent()
        self.agent("other.md", "other")
        config = config_from_mapping(self.raw, self.config)
        apply_agent_sync_plan(build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False))
        source.unlink()
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        self.assertIn("stale or invalid managed agent link", " ".join(plan.errors))
        self.assertTrue((self.destination / "reviewer.md").is_symlink())

    def test_native_format_change_blocks_old_projection(self):
        self.agent()
        self.raw["agent_formats"] = {"claude": "opencode"}
        config = config_from_mapping(self.raw, self.config)
        apply_agent_sync_plan(build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False))
        self.raw["agent_formats"] = {"claude": "codex"}
        plan = build_agent_sync_plan(config_from_mapping(self.raw, self.config), ("bbh",), ("claude",), dry_run=False)
        self.assertIn("stale or invalid managed agent", " ".join(plan.errors))
        self.assertTrue((self.destination / "reviewer.md").exists())
        self.assertFalse((self.destination / "reviewer.toml").exists())

    def test_sync_all_prints_agent_backup_without_skill_adoption(self):
        self.agent()
        self.destination.mkdir(parents=True)
        (self.destination / "reviewer.md").write_text("local")
        self.raw.pop("ai_skill_paths")
        self.raw["profile_sets"] = {"work": {"destination": "claude", "sources": [{"bridge": "bbh"}]}}
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        result = self.cli("sync", "all", "--adopt")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        backups = list((self.base / "home" / ".cache" / "aiskillsync-migration").glob("*/agents/claude/reviewer.md"))
        self.assertEqual(len(backups), 1)
        self.assertIn(str(backups[0].parents[1]), result.stdout)
        self.assertEqual(backups[0].read_text(), "local")

    def test_adoption_failure_reports_backup_location(self):
        self.agent()
        self.destination.mkdir(parents=True)
        target = self.destination / "reviewer.md"
        target.write_text("local")
        backup = self.base / "backups"
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False,
                                     adopt=True, backup_root=backup)
        with patch.object(Path, "symlink_to", side_effect=OSError("injected failure")):
            with self.assertRaisesRegex(ValueError, "adoption backups:.*backups/agents"):
                apply_agent_sync_plan(plan)
        self.assertEqual(target.read_text(), "local")

    def test_native_format_config_roundtrips_and_rejects_invalid(self):
        self.raw["agent_formats"] = {"claude": "opencode"}
        config = config_from_mapping(self.raw, self.config)
        self.assertEqual(config_from_mapping(parse_simple_yaml(config_to_text(config)), self.config), config)
        for formats in ({"missing": "codex"}, {"claude": "omp"}, {"claude": "pi"}, {"claude": []}, []):
            with self.subTest(formats=formats), self.assertRaises(ConfigError):
                self.raw["agent_formats"] = formats
                config_from_mapping(self.raw, self.config)

    @unittest.skipUnless(importlib.util.find_spec("tomllib"), "tomllib requires Python 3.11+")
    def test_sync_all_projects_native_destinations_in_profile(self):
        import tomllib
        source = self.agent()
        skill = self.source / "skills" / "safe"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("safe")
        self.raw["ai_agent_paths"]["codex"] = str(self.base / "codex" / "agents")
        self.raw["agent_formats"] = {"claude": "opencode", "codex": "codex"}
        self.raw["profile_sets"] = {"work": {"destinations": ["claude", "codex"],
                                            "sources": [{"bridge": "bbh"}]}}
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        result = self.cli("sync", "all")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("agent projection(s)", result.stdout)
        markdown = (self.destination / "reviewer.md").read_text()
        self.assertIn("mode: subagent\npermission:\n  task: deny", markdown)
        self.assertTrue(markdown.endswith(source.read_text().split("---\n", 2)[2]))
        parsed = tomllib.loads((self.base / "codex" / "agents" / "reviewer.toml").read_text())
        self.assertEqual(parsed["developer_instructions"], source.read_text().split("---\n", 2)[2])
        again = self.cli("sync", "all", "--dry-run")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(again.stdout.count("SKIP agent"), 2)

    @unittest.skipUnless(os.environ.get("OPENCODE_TEST_BIN"), "set OPENCODE_TEST_BIN for native loader test")
    def test_opencode_native_loader_discovers_projected_subagent(self):
        source = self.agent()
        self.raw["agent_formats"] = {"claude": "opencode"}
        self.raw["ai_agent_paths"]["claude"] = str(self.base / "config" / "opencode" / "agents")
        config = config_from_mapping(self.raw, self.config)
        apply_agent_sync_plan(build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False))
        target = Path(self.raw["ai_agent_paths"]["claude"]) / "reviewer.md"
        self.assertTrue(target.read_text().endswith(source.read_text().split("---\n", 2)[2]))
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.base / "home"),
               "TMPDIR": str(self.base), "XDG_CONFIG_HOME": str(self.base / "config"),
               "XDG_DATA_HOME": str(self.base / "data"), "XDG_CACHE_HOME": str(self.base / "cache"),
               "XDG_STATE_HOME": str(self.base / "state"), "OPENCODE_DISABLE_MODELS_FETCH": "true",
               "OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_CLAUDE_CODE": "true"}
        result = subprocess.run([os.environ["OPENCODE_TEST_BIN"], "agent", "list", "--pure"], cwd=self.base,
                                env=env, text=True, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("reviewer", result.stdout)

    def test_duplicate_agent_names_block_the_whole_apply(self):
        self.agent("one.md", "reviewer")
        self.agent("two.md", "reviewer")
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        self.assertTrue(plan.has_blockers)
        self.assertIn("duplicate agent name", " ".join(plan.errors))
        with self.assertRaises(ValueError):
            apply_agent_sync_plan(plan)
        self.assertFalse(self.destination.exists())

    def test_adopt_backs_up_unexpected_symlink_without_touching_its_target(self):
        source = self.agent()
        self.destination.mkdir(parents=True)
        foreign = self.base / "foreign.md"
        foreign.write_text("keep", encoding="utf-8")
        target = self.destination / "reviewer.md"
        target.symlink_to(foreign)
        config = config_from_mapping(self.raw, self.config)
        blocked = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        self.assertTrue(blocked.has_blockers)
        backup = self.base / "backups"
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False,
                                     adopt=True, backup_root=backup)
        apply_agent_sync_plan(plan)
        self.assertEqual(target.resolve(), source)
        saved = backup / "agents" / "claude" / "reviewer.md"
        self.assertTrue(saved.is_symlink())
        self.assertEqual(saved.readlink(), foreign)
        self.assertEqual(foreign.read_text(), "keep")

    def test_profiles_isolate_sources_and_destinations(self):
        source = self.agent()
        other = self.base / "other"
        (other / "agents").mkdir(parents=True)
        (other / "agents" / "private.md").write_text(
            "---\nname: private\ndescription: Another profile\n---\nPrivate.\n", encoding="utf-8")
        self.raw["bridges"].append({"name": "other", "path": str(other), "agents_path": "agents"})
        self.raw["ai_skill_paths"]["other"] = str(self.base / "other-skills")
        self.raw["ai_agent_paths"]["other"] = str(self.base / "other-agents")
        self.raw["profile_sets"] = {
            "work": {"destination": "claude", "sources": [{"bridge": "bbh", "include_skills": ["unrelated-skill"]}]},
            "personal": {"destination": "other", "sources": [{"bridge": "other"}]},
        }
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        bypass = self.cli("agents", "--repo", "other", "--dest", "claude")
        self.assertNotEqual(bypass.returncode, 0)
        result = self.cli("agents", "--profile", "work")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual((self.destination / "reviewer.md").resolve(), source)
        self.assertFalse((self.destination / "private.md").exists())
        self.assertFalse((self.base / "other-agents").exists())
        self.assertFalse((self.base / "claude" / "skills").exists())

    def test_agent_only_profile_destinations_roundtrip_without_skill_side_effects(self):
        self.agent()
        self.raw.pop("ai_skill_paths")
        self.raw["profile_sets"] = {"work": {"destination": "claude", "sources": [{"bridge": "bbh"}]}}
        config = config_from_mapping(self.raw, self.config)
        self.config.write_text(config_to_text(config))
        result = self.cli("agents", "--profile", "work")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue((self.destination / "reviewer.md").is_symlink())
        restored = self.cli("config")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertIn("AI agent paths:", restored.stdout)

    def test_agent_cli_clones_only_configured_agent_sources(self):
        self.agent()
        subprocess.run(["git", "init", "-b", "main", str(self.source)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.source), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-m", "agent fixture"], check=True, capture_output=True)
        checkout = self.base / "checkout"
        self.raw["bridges"][0].update(repo=self.source.as_uri(), path=str(checkout), branch="main")
        self.raw["sync"].update(clone_if_missing=True)
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        preview = self.cli("agents", "--dest", "claude", "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stderr + preview.stdout)
        self.assertFalse(checkout.exists())
        self.assertFalse(self.destination.exists())
        applied = self.cli("agents", "--dest", "claude")
        self.assertEqual(applied.returncode, 0, applied.stderr + applied.stdout)
        self.assertEqual((self.destination / "reviewer.md").resolve(), checkout / "agents" / "reviewer.md")
        self.assertFalse((self.base / "claude" / "skills").exists())
        (self.agents / "reviewer.md").write_text("---\nname: reviewer\ndescription: Updated reviewer\n---\nUpdated.\n")
        subprocess.run(["git", "-C", str(self.source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.source), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-m", "update fixture"], check=True, capture_output=True)
        self.raw["sync"]["pull_before_sync"] = True
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        updated = self.cli("agents", "--dest", "claude")
        self.assertEqual(updated.returncode, 0, updated.stderr + updated.stdout)
        self.assertIn("Updated reviewer", (self.destination / "reviewer.md").read_text())
        self.assertNotIn("linked skills", updated.stdout)

    def test_filtered_adoption_dry_run_preserves_unselected_and_unmanaged_files(self):
        source = self.agent()
        self.agent("other.md", "other")
        self.destination.mkdir(parents=True)
        (self.destination / "reviewer.md").write_text("local reviewer")
        (self.destination / "other.md").write_text("local other")
        (self.destination / "unmanaged.md").write_text("private")
        stale = self.destination / "stale.md"
        stale.symlink_to(self.base / "foreign-gone.md")
        self.write_config()
        # Directory and file reads can update atime; compare durable state.
        def snapshot():
            return {p: (s.st_mode, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                    for p in self.base.rglob("*") for s in (p.lstat(),)}
        before = snapshot()
        result = self.cli("agents", "--dest", "claude", "--agent", "reviewer", "--adopt", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(snapshot(), before)
        applied = self.cli("agents", "--dest", "claude", "--agent", "reviewer", "--adopt")
        self.assertEqual(applied.returncode, 0, applied.stderr + applied.stdout)
        self.assertEqual((self.destination / "reviewer.md").resolve(), source)
        self.assertEqual((self.destination / "other.md").read_text(), "local other")
        self.assertEqual((self.destination / "unmanaged.md").read_text(), "private")
        self.assertTrue(stale.is_symlink())
        backups = list((self.base / "home" / ".cache" / "aiskillsync-migration").glob("*/agents/claude/reviewer.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "local reviewer")
        missing = self.cli("agents", "--dest", "claude", "--agent", "missing", "--adopt")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("selected agent names not found", missing.stdout)

    def test_apply_rejects_changed_adoption_before_any_mutation(self):
        self.agent()
        self.destination.mkdir(parents=True)
        target = self.destination / "reviewer.md"
        target.write_text("before")
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False,
                                     adopt=True, backup_root=self.base / "backups")
        target.write_text("changed since planning")
        with self.assertRaisesRegex(ValueError, "destination changed"):
            apply_agent_sync_plan(plan)
        self.assertEqual(target.read_text(), "changed since planning")
        self.assertFalse((self.base / "backups").exists())

    def test_sync_all_projects_skills_and_agents_to_mixed_profile_destinations(self):
        agent = self.agent()
        skill = self.source / "skills" / "safe"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("safe")
        self.raw["ai_agent_paths"]["agents-only"] = str(self.base / "agents-only")
        self.raw["profile_sets"] = {"work": {
            "destinations": ["claude", "agents-only"], "sources": [{"bridge": "bbh"}]}}
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        preview = self.cli("sync", "all", "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stderr + preview.stdout)
        self.assertFalse(self.destination.exists())
        self.assertFalse((self.base / "claude" / "skills").exists())
        result = self.cli("sync", "all")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue((self.base / "claude" / "skills" / "safe").is_symlink())
        self.assertEqual((self.destination / "reviewer.md").resolve(), agent)
        self.assertEqual((self.base / "agents-only" / "reviewer.md").resolve(), agent)
        again = self.cli("sync", "all", "--dry-run")
        self.assertEqual(again.returncode, 0, again.stderr + again.stdout)
        self.assertIn("SKIP agent", again.stdout)

    def test_native_sync_all_rejects_projected_skill_overlap(self):
        self.agent()
        skill = self.source / "skills" / "safe"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("safe")
        root = self.base / "dest"
        self.raw["ai_skill_paths"]["claude"] = str(root)
        self.raw["ai_agent_paths"]["claude"] = str(root / "safe" / "agents")
        self.raw["sync"]["default_destinations"] = ["claude"]
        for provider in ("codex", "opencode"):
            with self.subTest(provider=provider):
                self.raw["agent_formats"] = {"claude": provider}
                self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
                for args in (("--dry-run",), ()):
                    result = self.cli("sync", "all", *args)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("agent destination", result.stdout + result.stderr)
                    self.assertFalse((skill / "agents").exists())
                    self.assertFalse((root / "safe").exists())

    def test_native_codex_destination_cannot_replace_other_source_symlink_chain(self):
        self.agent()
        self.destination.mkdir(parents=True)
        original = self.base / "original.md"
        original.write_text("---\nname: reviewer\ndescription: Original\n---\nORIGINAL\n")
        intermediate = self.destination / "reviewer.toml"
        intermediate.symlink_to(original)
        other = self.base / "other"
        (other / "agents").mkdir(parents=True)
        other_source = other / "agents" / "reviewer.md"
        other_source.symlink_to(intermediate)
        self.raw["bridges"].append({"name": "other", "path": str(other), "agents_path": "agents"})
        self.raw["agent_formats"] = {"claude": "codex"}
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False,
                                     adopt=True, backup_root=self.base / "backup")
        self.assertIn("would replace a source file", " ".join(plan.errors))
        with self.assertRaises(ValueError):
            apply_agent_sync_plan(plan)
        self.assertEqual(intermediate.readlink(), original)
        self.assertIn("ORIGINAL", other_source.read_text())

    def test_sync_all_rejects_agent_root_under_projected_skill(self):
        self.agent()
        skill = self.source / "skills" / "safe"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("safe")
        skills_dest = self.base / "dest"
        self.raw["ai_skill_paths"]["claude"] = str(skills_dest)
        self.raw["ai_agent_paths"]["claude"] = str(skills_dest / "safe" / "agents")
        self.raw["sync"]["default_destinations"] = ["claude"]
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        for args in (("--dry-run",), (), ()):
            result = self.cli("sync", "all", *args)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("agent destination", result.stdout + result.stderr)
            self.assertFalse((skill / "agents").exists())
            self.assertFalse((skills_dest / "safe").exists())

    def test_sync_all_rejects_existing_skill_symlink_and_aliased_agent_root(self):
        self.agent()
        skill = self.source / "skills" / "safe"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("safe")
        root = self.base / "dest"
        root.mkdir()
        (root / "safe").symlink_to(skill, target_is_directory=True)
        alias = self.base / "alias"
        alias.symlink_to(root, target_is_directory=True)
        self.raw["ai_skill_paths"]["claude"] = str(root)
        self.raw["ai_agent_paths"]["claude"] = str(alias / "safe" / "agents")
        self.raw["sync"]["default_destinations"] = ["claude"]
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        result = self.cli("sync", "all")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("agent destination", result.stdout + result.stderr)
        self.assertFalse((skill / "agents").exists())
        self.assertTrue((root / "safe").is_symlink())

    def test_sync_all_rejects_agent_root_inside_unlinked_skill_source(self):
        self.agent()
        skill = self.source / "skills" / "safe"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("safe")
        alias = self.base / "source-alias"
        alias.symlink_to(skill, target_is_directory=True)
        self.raw["ai_skill_paths"]["claude"] = str(self.base / "dest")
        self.raw["sync"]["default_destinations"] = ["claude"]
        for agent_root in (skill / "agents", alias / "agents"):
            with self.subTest(agent_root=agent_root):
                self.raw["ai_agent_paths"]["claude"] = str(agent_root)
                self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
                for args in (("--dry-run",), ()):
                    result = self.cli("sync", "all", *args)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertFalse((skill / "agents").exists())
                    self.assertFalse((self.base / "dest" / "safe").exists())

    def test_sync_all_prints_agent_backup_path_without_skill_adoptions(self):
        self.agent()
        skill = self.source / "skills" / "safe"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("safe")
        self.destination.mkdir(parents=True)
        existing = self.destination / "reviewer.md"
        existing.write_text("preserve local")
        self.raw["sync"]["default_destinations"] = ["claude"]
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        result = self.cli("sync", "all", "--adopt")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        backups = list((self.base / "home" / ".cache" / "aiskillsync-migration").glob("*/agents/claude/reviewer.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "preserve local")
        self.assertIn(str(backups[0].parents[1]), result.stdout)

    def test_agent_sync_rejects_destinations_inside_agent_source_root(self):
        agent = self.agent()
        alias = self.base / "source-alias"
        alias.symlink_to(self.source, target_is_directory=True)
        self.raw["sync"]["default_destinations"] = ["claude"]
        for agent_root in (self.agents, alias / "agents", self.agents / "nested"):
            with self.subTest(agent_root=agent_root):
                self.raw["ai_agent_paths"]["claude"] = str(agent_root)
                self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
                for args in (("agents", "--dest", "claude", "--adopt", "--dry-run"),
                             ("agents", "--dest", "claude", "--adopt"),
                             ("sync", "all", "--adopt", "--dry-run"),
                             ("sync", "all", "--adopt")):
                    result = self.cli(*args)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertTrue(agent.is_file())
                    self.assertFalse(agent.is_symlink())
                    self.assertIn("Review safely.", agent.read_text())

    def test_agent_sync_protects_symlinked_source_paths(self):
        self.raw["sync"]["default_destinations"] = ["claude"]
        external = self.base / "external"
        external.mkdir()
        alias = self.base / "external-alias"
        alias.symlink_to(external, target_is_directory=True)
        source_file = external / "reviewer.md"
        source_file.write_text("---\nname: reviewer\ndescription: Test\n---\nKeep source.\n")
        for mode in ("directory", "file"):
            with self.subTest(mode=mode):
                if mode == "directory":
                    self.agents.rmdir()
                    self.agents.symlink_to(external, target_is_directory=True)
                else:
                    self.agents.unlink()
                    self.agents.mkdir()
                    (self.agents / "reviewer.md").symlink_to(source_file)
                for dest in (external, alias):
                    self.raw["ai_agent_paths"]["claude"] = str(dest)
                    self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
                    for args in (("agents", "--dest", "claude", "--adopt"),
                                 ("sync", "all", "--adopt")):
                        result = self.cli(*args)
                        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertFalse(source_file.is_symlink())
                        self.assertIn("Keep source.", source_file.read_text())

    def test_agent_sync_allows_separate_project_local_agent_destination(self):
        agent = self.agent()
        self.raw["ai_agent_paths"]["claude"] = str(self.source / ".claude" / "agents")
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        result = self.cli("agents", "--dest", "claude")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.source / ".claude" / "agents" / "reviewer.md").resolve(), agent)

    def test_agent_adoption_does_not_redirect_another_source_symlink_chain(self):
        agent = self.agent()
        other = self.base / "other"
        (other / "agents").mkdir(parents=True)
        self.destination.mkdir(parents=True)
        original = self.base / "original.md"
        original.write_text("---\nname: reviewer\ndescription: Original\n---\nORIGINAL\n")
        intermediate = self.destination / "reviewer.md"
        intermediate.symlink_to(original)
        other_source = other / "agents" / "reviewer.md"
        other_source.symlink_to(intermediate)
        self.raw["bridges"].append({"name": "other", "path": str(other), "agents_path": "agents"})
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False,
                                     adopt=True, backup_root=self.base / "backup")
        self.assertTrue(plan.has_blockers)
        with self.assertRaises(ValueError):
            apply_agent_sync_plan(plan)
        self.assertEqual(intermediate.readlink(), original)
        self.assertIn("ORIGINAL", other_source.read_text())
        self.assertIn("Review safely.", agent.read_text())

    def test_agent_plan_respects_explicit_lane_override(self):
        self.agent()
        other = self.base / "beta"
        (other / "agents").mkdir(parents=True)
        beta_agent = other / "agents" / "reviewer.md"
        beta_agent.write_text("---\nname: reviewer\ndescription: Beta\n---\n")
        self.raw["bridges"].append({"name": "beta", "path": str(other), "agents_path": "agents"})
        self.raw["lanes"] = {"group": {"default": "stable", "variants": {"stable": "bbh", "beta": "beta"}}}
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, (), ("claude",), dry_run=False,
                                     lane_overrides={"group": "beta"})
        self.assertFalse(plan.has_blockers, plan.errors)
        self.assertEqual(plan.actions[0].agent.path, beta_agent)

    def test_sync_all_honors_lane_override_for_skill_and_agent_plans(self):
        from contextlib import redirect_stdout, redirect_stderr
        from io import StringIO
        from unittest.mock import patch
        from aiskillsync.cli import build_parser, cmd_sync

        self.agent()
        other = self.base / "beta"
        (other / "agents").mkdir(parents=True)
        beta_agent = other / "agents" / "reviewer.md"
        beta_agent.write_text("---\nname: reviewer\ndescription: Beta\n---\n")
        for root in (self.source, other):
            skill = root / "skills" / "safe"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("safe")
        self.raw["bridges"].append({"name": "beta", "path": str(other), "agents_path": "agents"})
        self.raw["lanes"] = {"group": {"default": "stable", "variants": {"stable": "bbh", "beta": "beta"}}}
        self.raw["sync"]["default_destinations"] = ["claude"]
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        args = build_parser().parse_args(["--config", str(self.config), "sync", "all"])
        args.lane_overrides = {"group": "beta"}
        out, err = StringIO(), StringIO()
        with patch.dict(os.environ, {"HOME": str(self.base / "home")}):
            with redirect_stdout(out), redirect_stderr(err):
                result = cmd_sync(args, out, err)
        self.assertEqual(result, 0, err.getvalue() + out.getvalue())
        self.assertEqual((self.destination / "reviewer.md").resolve(), beta_agent)
        self.assertEqual((self.base / "claude" / "skills" / "safe").resolve(), other / "skills" / "safe")

    def test_sync_all_allows_nested_agent_root_outside_skill_action(self):
        agent = self.agent()
        skill = self.source / "skills" / "safe"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("safe")
        root = self.base / "dest"
        self.raw["ai_skill_paths"]["claude"] = str(root)
        self.raw["ai_agent_paths"]["claude"] = str(root / "unrelated" / "agents")
        self.raw["sync"]["default_destinations"] = ["claude"]
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        result = self.cli("sync", "all")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual((root / "unrelated" / "agents" / "reviewer.md").resolve(), agent)

    def test_sync_all_legacy_agent_only_without_skill_destinations(self):
        source = self.agent()
        self.raw.pop("ai_skill_paths")
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        result = self.cli("sync", "all")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual((self.destination / "reviewer.md").resolve(), source)

    def test_sync_all_skill_filters_never_sync_agents(self):
        self.agent()
        skill = self.source / "skills" / "safe"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("safe")
        self.raw["sync"]["default_destinations"] = ["claude"]
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        denylist = self.base / "denylist"
        denylist.write_text("safe\n")
        for args in (("--skill", "safe"), ("--exclude-skill", "safe"), ("--denylist", str(denylist))):
            with self.subTest(args=args):
                result = self.cli("sync", "all", *args)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertFalse(self.destination.exists())

    def test_sync_all_configured_migration_denylist_suppresses_agents(self):
        self.agent()
        skill = self.source / "skills" / "safe"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("safe")
        self.raw["sync"].update(default_destinations=["claude"], migration_denylist=["safe"])
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        for option in ("--adopt", "--overwrite"):
            with self.subTest(option=option):
                result = self.cli("sync", "all", option)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertFalse(self.destination.exists())

    def test_switch_does_not_touch_agents_even_when_local_or_missing(self):
        self.agent()
        other = self.base / "beta"
        (other / "agents").mkdir(parents=True)
        (other / "agents" / "reviewer.md").write_text("---\nname: reviewer\ndescription: Beta\n---\n")
        for root in (self.source, other):
            skill = root / "skills" / "safe"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("safe")
        self.raw["bridges"].append({"name": "beta", "path": str(other), "agents_path": "agents"})
        self.raw["lanes"] = {"group": {"default": "stable", "variants": {"stable": "bbh", "beta": "beta"}}}
        self.raw["sync"]["default_destinations"] = ["claude"]
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        result = self.cli("switch", "beta")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertFalse(self.destination.exists())
        self.destination.mkdir(parents=True)
        local = self.destination / "reviewer.md"
        local.write_text("local agent")
        result = self.cli("switch", "stable")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertFalse(local.is_symlink())
        self.assertEqual(local.read_text(), "local agent")

    def test_sync_all_supports_agent_only_profile(self):
        source = self.agent()
        self.raw.pop("ai_skill_paths")
        self.raw["profile_sets"] = {"work": {"destination": "claude", "sources": [{"bridge": "bbh"}]}}
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        result = self.cli("sync", "all")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue((self.destination / "reviewer.md").is_symlink())
        self.assertEqual((self.destination / "reviewer.md").resolve(), source)

    def test_sync_all_legacy_configuration_includes_agents(self):
        source = self.agent()
        skill = self.source / "skills" / "safe"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("safe")
        self.raw["sync"]["default_destinations"] = ["claude"]
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        result = self.cli("sync", "all")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue((self.base / "claude" / "skills" / "safe").is_symlink())
        self.assertEqual((self.destination / "reviewer.md").resolve(), source)

    def test_sync_all_keeps_profile_agent_sources_isolated(self):
        source = self.agent()
        other = self.base / "other"
        (other / "agents").mkdir(parents=True)
        other_agent = other / "agents" / "other.md"
        other_agent.write_text("---\nname: other\ndescription: Other role\n---\nOther.\n")
        for root in (self.source, other):
            skill = root / "skills" / "safe"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("safe")
        self.raw["bridges"].append({"name": "other", "path": str(other), "agents_path": "agents"})
        self.raw["ai_skill_paths"]["other"] = str(self.base / "other-skills")
        self.raw["ai_agent_paths"]["other"] = str(self.base / "other-agents")
        self.raw["profile_sets"] = {
            "work": {"destination": "claude", "sources": [{"bridge": "bbh"}]},
            "personal": {"destination": "other", "sources": [{"bridge": "other"}]},
        }
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        result = self.cli("sync", "all")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual((self.destination / "reviewer.md").resolve(), source)
        self.assertEqual((self.base / "other-agents" / "other.md").resolve(), other_agent)
        self.assertFalse((self.destination / "other.md").exists())
        self.assertFalse((self.base / "other-agents" / "reviewer.md").exists())

    def test_sync_all_agent_conflict_blocks_skill_and_agent_writes(self):
        self.agent()
        skill = self.source / "skills" / "safe"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("safe")
        self.destination.mkdir(parents=True)
        existing = self.destination / "reviewer.md"
        existing.write_text("local agent")
        self.raw["profile_sets"] = {"work": {
            "destination": "claude", "sources": [{"bridge": "bbh"}]}}
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        result = self.cli("sync", "all")
        self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(existing.read_text(), "local agent")
        self.assertFalse((self.base / "claude" / "skills" / "safe").exists())

    def test_config_rejects_shared_agent_roots_and_agent_skill_root_aliases(self):
        self.raw["ai_agent_paths"]["alias"] = str(self.destination)
        with self.assertRaisesRegex(ConfigError, "share.*root"):
            config_from_mapping(self.raw, self.config)
        self.raw["ai_agent_paths"].pop("alias")
        self.raw["ai_agent_paths"]["claude"] = self.raw["ai_skill_paths"]["claude"]
        with self.assertRaisesRegex(ConfigError, "share.*root"):
            config_from_mapping(self.raw, self.config)

    def test_discovery_validates_agent_metadata_not_unrelated_markdown_or_code(self):
        self.agent("definition.md", "reviewer", ">\n  Review changes\n  with care.")
        (self.agents / "index.md").write_text("# Index\nDo not install")
        (self.agents / "helper.py").write_text("print('not an agent')")
        nested = self.agents / "nested"
        nested.mkdir()
        (nested / "nested.md").write_text("---\nname: nested\ndescription: Not a direct child\n---\n")
        invalid = {
            "missing-name.md": "---\ndescription: Missing name\n---\n",
            "missing-description.md": "---\nname: missing-description\n---\n",
            "escape.md": "---\nname: ../escape\ndescription: Unsafe name\n---\n",
            "unterminated.md": "---\nname: unterminated\ndescription: No closing delimiter\n",
            "empty-block.md": "---\nname: empty-block\ndescription: |\n---\n",
            "duplicate-key.md": "---\nname: first\nname: second\ndescription: Duplicate name\n---\n",
        }
        for filename, text in invalid.items():
            (self.agents / filename).write_text(text)
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        self.assertFalse(plan.has_blockers, plan.errors)
        self.assertEqual([a.agent.name for a in plan.actions], ["reviewer"])
        self.assertEqual(plan.actions[0].agent.description, "Review changes with care.")
        apply_agent_sync_plan(plan)
        self.assertEqual([p.name for p in self.destination.iterdir()], ["reviewer.md"])

    def test_non_directory_destination_is_a_clean_blocker(self):
        self.agent()
        self.destination.parent.mkdir(parents=True)
        self.destination.write_text("not a directory")
        self.write_config()
        result = self.cli("agents", "--dest", "claude")
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertIn("root path is not a directory", result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.destination.read_text(), "not a directory")

    def test_branch_pin_blocks_wrong_detached_and_non_git_sources(self):
        self.agent()
        self.raw["bridges"][0]["branch"] = "main"
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        self.assertTrue(plan.has_blockers)
        self.assertIn("requires a git checkout", " ".join(plan.errors))
        subprocess.run(["git", "init", "-b", "wrong", str(self.source)], check=True, capture_output=True)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        self.assertIn("checked out 'wrong'", " ".join(plan.errors))
        subprocess.run(["git", "-C", str(self.source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.source), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-m", "branch fixture"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.source), "checkout", "--detach"], check=True, capture_output=True)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        self.assertIn("checkout is detached", " ".join(plan.errors))
        self.assertFalse(self.destination.exists())

    def test_only_active_lane_is_discovered_from_profile_sources(self):
        self.agent()
        self.raw["bridges"].append({"name": "inactive", "path": str(self.base / "missing"),
                                     "agents_path": "agents", "branch": "wrong"})
        self.raw["lanes"] = {"bbh": {"default": "stable", "variants": {"stable": "bbh", "beta": "inactive"}}}
        self.raw["profile_sets"] = {"work": {"destination": "claude", "sources": [
            {"bridge": "bbh"}, {"bridge": "inactive"}]}}
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, (), (), profile_name="work", dry_run=False)
        self.assertFalse(plan.has_blockers, plan.errors)
        self.assertEqual([a.agent.bridge.name for a in plan.actions], ["bbh"])
        apply_agent_sync_plan(plan)
        self.assertTrue((self.destination / "reviewer.md").is_symlink())

    def test_duplicate_names_across_selected_bridges_block(self):
        self.agent()
        other = self.base / "other"
        (other / "agents").mkdir(parents=True)
        (other / "agents" / "reviewer.md").write_text("---\nname: reviewer\ndescription: Also reviewer\n---\n")
        self.raw["bridges"].append({"name": "other", "path": str(other), "agents_path": "agents"})
        plan = build_agent_sync_plan(config_from_mapping(self.raw, self.config), ("all",), ("claude",), dry_run=False)
        self.assertTrue(plan.has_blockers)
        self.assertIn("duplicate agent name", " ".join(plan.errors))
        self.assertFalse(self.destination.exists())

    def test_missing_empty_and_invalid_agent_sources_block_without_writes(self):
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        self.assertIn("no valid agent definitions", " ".join(plan.errors))
        (self.agents / "invalid.md").write_text("---\nname: missing-description\n---\n")
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        self.assertTrue(plan.has_blockers)
        self.assertIn("invalid agent definition", " ".join(plan.notices))
        (self.agents / "invalid.md").unlink()
        self.agents.rmdir()
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False)
        self.assertIn("agents path missing", " ".join(plan.errors))
        self.assertFalse(self.destination.exists())

    def test_dry_run_plan_cannot_be_applied(self):
        self.agent()
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=True)
        self.assertEqual(apply_agent_sync_plan(plan), ())
        self.assertFalse(self.destination.exists())

    def test_no_implicit_destinations_and_no_default_config_creation_on_preview(self):
        self.agent()
        self.write_config()
        for args in (("agents", "--dry-run"), ("agents", "--dest", "codex", "--dry-run")):
            result = self.cli(*args)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        home = self.base / "empty-home"
        result = subprocess.run([sys.executable, "-m", "aiskillsync", "agents", "--dest", "claude", "--dry-run"],
                                cwd=ROOT, env={**os.environ, "HOME": str(home)}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(home.exists())
        self.assertFalse(self.destination.exists())

    def test_config_roundtrip_and_agent_field_validation(self):
        config = config_from_mapping(self.raw, self.config)
        restored = config_from_mapping(parse_simple_yaml(config_to_text(config)), self.config)
        self.assertEqual(restored, config)
        for invalid in (False, [], ""):
            with self.subTest(agents_path=invalid):
                self.raw["bridges"][0]["agents_path"] = invalid
                with self.assertRaises(ConfigError):
                    config_from_mapping(self.raw, self.config)
        self.raw["bridges"][0]["agents_path"] = "agents"
        for invalid in ([], {"claude": ""}, {"../escape": str(self.destination)}):
            with self.subTest(ai_agent_paths=invalid):
                self.raw["ai_agent_paths"] = invalid
                with self.assertRaises(ConfigError):
                    config_from_mapping(self.raw, self.config)

    def test_dangling_symlink_adoption_and_backup_collision_safety(self):
        self.agent()
        self.destination.mkdir(parents=True)
        target = self.destination / "reviewer.md"
        missing = self.base / "missing.md"
        target.symlink_to(missing)
        backup_root = self.base / "backups"
        config = config_from_mapping(self.raw, self.config)
        plan = build_agent_sync_plan(config, ("bbh",), ("claude",), dry_run=False, adopt=True, backup_root=backup_root)
        saved = backup_root / "agents" / "claude" / "reviewer.md"
        saved.parent.mkdir(parents=True)
        saved.write_text("older backup")
        with self.assertRaisesRegex(ValueError, "backup already exists"):
            apply_agent_sync_plan(plan)
        self.assertEqual(saved.read_text(), "older backup")
        self.assertEqual(target.readlink(), missing)
        saved.unlink()
        apply_agent_sync_plan(plan)
        self.assertTrue(saved.is_symlink())
        self.assertEqual(saved.readlink(), missing)
        self.assertTrue(target.is_file())

    def test_agent_cli_links_files_independently_and_repeats_as_noop(self):
        source = self.agent()
        self.write_config()
        preview = self.cli("agents", "--repo", "bbh", "--dest", "claude", "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse(self.destination.exists())
        applied = self.cli("agents", "--repo", "bbh", "--dest", "claude")
        self.assertEqual(applied.returncode, 0, applied.stderr + applied.stdout)
        target = self.destination / "reviewer.md"
        self.assertTrue(target.is_symlink())
        self.assertEqual(target.resolve(), source)
        self.assertFalse((self.base / "claude" / "skills").exists())
        before = target.lstat().st_mtime_ns
        repeated = self.cli("agents", "--repo", "bbh", "--dest", "claude")
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn("No agent symlinks needed", repeated.stdout)
        self.assertEqual(target.lstat().st_mtime_ns, before)


if __name__ == "__main__":
    unittest.main()
