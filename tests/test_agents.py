from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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
        stale.symlink_to(self.agents / "gone.md")
        self.write_config()
        before = {p: p.lstat() for p in self.base.rglob("*")}
        result = self.cli("agents", "--dest", "claude", "--agent", "reviewer", "--adopt", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual({p: p.lstat() for p in self.base.rglob("*")}, before)
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

    def test_skill_sync_ignores_agent_only_destinations_in_mixed_profiles(self):
        self.agent()
        skill = self.source / "skills" / "safe"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("safe")
        self.raw["ai_agent_paths"]["agents-only"] = str(self.base / "agents-only")
        self.raw["profile_sets"] = {"work": {
            "destinations": ["claude", "agents-only"], "sources": [{"bridge": "bbh"}]}}
        self.config.write_text(config_to_text(config_from_mapping(self.raw, self.config)))
        result = self.cli("sync", "all")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue((self.base / "claude" / "skills" / "safe").is_symlink())
        self.assertFalse(self.destination.exists())
        self.assertFalse((self.base / "agents-only").exists())

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
