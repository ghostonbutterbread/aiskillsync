from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ManagedCopyWorkflowTests(unittest.TestCase):
    def test_explicit_copy_destination_projects_nested_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source" / "skills" / "example"
            (source / "references").mkdir(parents=True)
            (source / "SKILL.md").write_text("hello", encoding="utf-8")
            (source / "references" / "guide.md").write_text("nested", encoding="utf-8")
            destination = base / "windows" / "skills"
            config = base / "config.yaml"
            config.write_text(f"""bridges:
  - name: source
    path: {base / 'source'}
    skills_path: skills
ai_skill_paths:
  windows: {destination}
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [windows]
  copy_destinations: [windows]
""", encoding="utf-8")
            env = dict(os.environ, HOME=str(base), PYTHONPATH=str(ROOT))
            result = subprocess.run([sys.executable, "-m", "aiskillsync", "--config", str(config),
                                     "sync", "all", "--apply"], cwd=ROOT, env=env,
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((destination / "example").is_symlink())
            self.assertEqual((destination / "example" / "references" / "guide.md").read_text(), "nested")


    def test_refresh_prune_and_preserve_local_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "repo" / "skills" / "example"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("v1")
            dest = base / "dest"
            config = base / "config.yaml"
            config.write_text(f"""bridges:
  - name: source
    path: {base / 'repo'}
ai_skill_paths:
  windows: {dest}
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [windows]
  copy_destinations: [windows]
""")
            env = dict(os.environ, HOME=str(base), PYTHONPATH=str(ROOT))
            def run(*args):
                return subprocess.run([sys.executable, "-m", "aiskillsync", "--config", str(config),
                                       "sync", "all", *args], cwd=ROOT, env=env, text=True, capture_output=True)
            self.assertEqual(run().returncode, 0)
            record = next((base / "managed-copies").glob("*.json"))
            before = (record.stat().st_mtime_ns, (dest / "example" / "SKILL.md").stat().st_mtime_ns)
            self.assertEqual(run().returncode, 0)
            self.assertEqual(before, (record.stat().st_mtime_ns, (dest / "example" / "SKILL.md").stat().st_mtime_ns))
            (source / "SKILL.md").write_text("v2")
            (source / "nested").mkdir(); (source / "nested" / "new").write_text("new")
            self.assertEqual(run("--dry-run").returncode, 0)
            self.assertEqual((dest / "example" / "SKILL.md").read_text(), "v1")
            self.assertEqual(run().returncode, 0)
            self.assertEqual((dest / "example" / "SKILL.md").read_text(), "v2")
            self.assertEqual((dest / "example" / "nested" / "new").read_text(), "new")
            (source / "nested" / "new").unlink(); (source / "nested").rmdir()
            self.assertEqual(run().returncode, 0)
            self.assertFalse((dest / "example" / "nested").exists())
            (dest / "example" / "local").write_text("mine")
            (source / "SKILL.md").write_text("v3")
            self.assertNotEqual(run().returncode, 0)
            self.assertEqual((dest / "example" / "local").read_text(), "mine")
            (dest / "example" / "local").unlink()
            (source / "SKILL.md").unlink()
            source.rmdir()
            pruned = run()
            self.assertEqual(pruned.returncode, 0, pruned.stdout + pruned.stderr)
            self.assertIn("pruned=1", pruned.stdout)
            self.assertFalse((dest / "example").exists())


    def test_profile_migrates_legacy_link_then_prunes_filtered_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); root = base / "repo" / "skills"; dest = base / "dest"
            for name in ("one", "two"):
                skill = root / name; skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(name)
            dest.mkdir(); (dest / "one").symlink_to(root / "one", target_is_directory=True)
            config = base / "config.yaml"
            def write(include):
                config.write_text(f"""bridges:
  - name: source
    path: {base / 'repo'}
ai_skill_paths:
  windows: {dest}
profile_sets:
  myprofile:
    destination: windows
    sources:
      - bridge: source
        include_skills: {include}
sync:
  pull_before_sync: false
  clone_if_missing: false
  copy_destinations: [windows]
""")
            env = dict(os.environ, HOME=str(base), PYTHONPATH=str(ROOT))
            def run():
                return subprocess.run([sys.executable, "-m", "aiskillsync", "--config", str(config),
                                       "sync", "--profile", "myprofile"], cwd=ROOT, env=env, text=True, capture_output=True)
            write("[one, two]")
            config.write_text(config.read_text().replace("  copy_destinations: [windows]\n", ""))
            result = run(); self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((dest / "two").is_symlink())
            write("[one, two]")
            result = run(); self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((dest / "one").is_symlink())
            write("[one]")
            result = run(); self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((dest / "two").exists())
            self.assertEqual((dest / "one" / "SKILL.md").read_text(), "one")

    def test_unowned_directory_is_not_adopted_even_with_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); source = base / "repo" / "skills" / "x"; source.mkdir(parents=True)
            (source / "SKILL.md").write_text("source")
            dest = base / "dest" / "x"; dest.mkdir(parents=True)
            (dest / "SKILL.md").write_text("mine")
            config = base / "config.yaml"
            config.write_text(f"""bridges:
  - name: source
    path: {base / 'repo'}
ai_skill_paths:
  windows: {dest.parent}
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [windows]
  copy_destinations: [windows]
""")
            result = subprocess.run([sys.executable, "-m", "aiskillsync", "--config", str(config),
                                     "sync", "all", "--adopt"], cwd=ROOT,
                                    env=dict(os.environ, HOME=str(base), PYTHONPATH=str(ROOT)), text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((dest / "SKILL.md").read_text(), "mine")


    def test_destination_under_other_skill_source_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); skills = base / "repo" / "skills"; source = skills / "x"
            source.mkdir(parents=True); (source / "SKILL.md").write_text("source")
            dest = skills / "nested" / "dest"
            config = base / "config.yaml"
            config.write_text(f"""bridges:
  - name: source
    path: {base / 'repo'}
ai_skill_paths:
  windows: {dest}
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [windows]
  copy_destinations: [windows]
""")
            result = subprocess.run([sys.executable, "-m", "aiskillsync", "--config", str(config), "sync", "all"],
                                    cwd=ROOT, env=dict(os.environ, HOME=str(base), PYTHONPATH=str(ROOT)),
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(dest.exists())


    def test_copy_destination_inside_repo_outside_skills_is_blocked(self) -> None:
        from aiskillsync.config import load_config
        from aiskillsync.sync import build_sync_plan
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); repo = base / "repo"; source = repo / "skills" / "x"
            source.mkdir(parents=True); (source / "SKILL.md").write_text("source")
            (source / "shared").symlink_to(repo / "exports", target_is_directory=True)
            (repo / "exports").mkdir()
            config = base / "config.yaml"
            config.write_text(f"""bridges:
  - name: source
    path: {repo}
ai_skill_paths:
  windows: {repo / 'exports'}
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [windows]
  copy_destinations: [windows]
""")
            self.assertTrue(build_sync_plan(load_config(config), ("all",), (), dry_run=False).has_blockers)

    def test_revalidation_rejects_changes_between_plan_and_apply(self) -> None:
        from aiskillsync.config import load_config
        from aiskillsync.sync import build_sync_plan, apply_sync_plan
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); source = base / "repo" / "skills" / "x"; source.mkdir(parents=True)
            (source / "SKILL.md").write_text("source")
            dest = base / "dest"; config = base / "config.yaml"
            config.write_text(f"""bridges:
  - name: source
    path: {base / 'repo'}
ai_skill_paths:
  windows: {dest}
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [windows]
  copy_destinations: [windows]
""")
            cfg = load_config(config)
            plan = build_sync_plan(cfg, ("all",), (), dry_run=False)
            (source / "SKILL.md").write_text("changed")
            with self.assertRaisesRegex(ValueError, "source changed"):
                apply_sync_plan(plan)
            self.assertFalse((dest / "x").exists())

    def test_nested_symlinks_within_repo_materialize_and_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); source = base / "repo" / "skills" / "x"; source.mkdir(parents=True)
            (source / "SKILL.md").write_text("source")
            shared = base / "repo" / "shared"; shared.mkdir()
            (shared / "guide").write_text("first")
            (source / "alias").symlink_to(shared / "guide")
            (source / "dir-link").symlink_to(shared, target_is_directory=True)
            dest = base / "dest"; config = base / "config.yaml"
            config.write_text(f"""bridges:
  - name: source
    path: {base / 'repo'}
ai_skill_paths:
  windows: {dest}
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [windows]
  copy_destinations: [windows]
""")
            result = subprocess.run([sys.executable, "-m", "aiskillsync", "--config", str(config), "sync", "all"],
                                    cwd=ROOT, env=dict(os.environ, HOME=str(base), PYTHONPATH=str(ROOT)),
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((dest / "x" / "alias").is_symlink())
            self.assertEqual((dest / "x" / "dir-link" / "guide").read_text(), "first")
            (shared / "guide").write_text("second")
            result = subprocess.run([sys.executable, "-m", "aiskillsync", "--config", str(config), "sync", "all"],
                                    cwd=ROOT, env=dict(os.environ, HOME=str(base), PYTHONPATH=str(ROOT)),
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((dest / "x" / "alias").read_text(), "second")


    def test_unsafe_source_links_and_special_files_block_before_copy(self) -> None:
        from aiskillsync.config import load_config
        from aiskillsync.sync import build_sync_plan
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); repo = base / "repo"; source = repo / "skills" / "x"
            source.mkdir(parents=True); (source / "SKILL.md").write_text("source")
            (base / "outside").write_text("outside")
            config = base / "config.yaml"; dest = base / "dest"
            config.write_text(f"""bridges:
  - name: source
    path: {repo}
ai_skill_paths:
  windows: {dest}
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [windows]
  copy_destinations: [windows]
""")
            cfg = load_config(config); link = source / "bad"
            for target in (base / "outside", repo / "missing", source):
                with self.subTest(target=target):
                    link.symlink_to(target)
                    plan = build_sync_plan(cfg, ("all",), (), dry_run=False)
                    self.assertTrue(plan.has_blockers, plan.errors)
                    self.assertFalse(dest.exists())
                    link.unlink()
            os.mkfifo(link)
            self.assertTrue(build_sync_plan(cfg, ("all",), (), dry_run=False).has_blockers)
            self.assertFalse(dest.exists())

    def test_executable_only_source_change_refreshes_and_local_mode_edit_blocks(self) -> None:
        from aiskillsync.config import load_config
        from aiskillsync.sync import build_sync_plan, apply_sync_plan
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); source = base / "repo" / "skills" / "x"; source.mkdir(parents=True)
            (source / "SKILL.md").write_text("source")
            script = source / "run"; script.write_text("#!/bin/sh\n"); script.chmod(0o644)
            dest = base / "dest"; config = base / "config.yaml"
            config.write_text(f"""bridges:
  - name: source
    path: {base / 'repo'}
ai_skill_paths:
  windows: {dest}
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [windows]
  copy_destinations: [windows]
""")
            cfg = load_config(config)
            apply_sync_plan(build_sync_plan(cfg, ("all",), (), dry_run=False))
            script.chmod(0o755)
            plan = build_sync_plan(cfg, ("all",), (), dry_run=False)
            self.assertEqual(plan.copies[0].action, "copy-refresh")
            apply_sync_plan(plan)
            self.assertEqual((dest / "x" / "run").stat().st_mode & 0o111, 0o111)
            self.assertFalse(build_sync_plan(cfg, ("all",), (), dry_run=False).copies)
            (dest / "x" / "run").chmod(0o644)
            self.assertTrue(build_sync_plan(cfg, ("all",), (), dry_run=False).has_blockers)

    def test_unsupported_destination_executable_bits_report_error(self) -> None:
        from unittest.mock import patch
        import shutil
        from aiskillsync.config import load_config
        from aiskillsync.sync import build_sync_plan, apply_sync_plan
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); source = base / "repo" / "skills" / "x"; source.mkdir(parents=True)
            (source / "SKILL.md").write_text("source")
            (source / "run").write_text("script"); (source / "run").chmod(0o755)
            dest = base / "dest"; config = base / "config.yaml"
            config.write_text(f"""bridges:
  - name: source
    path: {base / 'repo'}
ai_skill_paths:
  windows: {dest}
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [windows]
  copy_destinations: [windows]
""")
            plan = build_sync_plan(load_config(config), ("all",), (), dry_run=False)
            original = shutil.copytree
            def lose_mode(*args, **kwargs):
                result = original(*args, **kwargs)
                (Path(args[1]) / "run").chmod(0o644)
                return result
            with patch("aiskillsync.managed_copies.shutil.copytree", side_effect=lose_mode):
                with self.assertRaisesRegex(ValueError, "executable.*destination"):
                    apply_sync_plan(plan)
            self.assertFalse((dest / "x").exists())

    def test_mount_detection_selects_copy_without_explicit_override(self) -> None:
        from unittest.mock import patch
        from aiskillsync.config import load_config
        from aiskillsync.sync import build_sync_plan, apply_sync_plan
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); source = base / "repo" / "skills" / "x"; source.mkdir(parents=True)
            (source / "SKILL.md").write_text("data")
            dest = base / "dest"; config = base / "config.yaml"
            config.write_text(f"""bridges:
  - name: source
    path: {base / 'repo'}
ai_skill_paths:
  windows: {dest}
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [windows]
""")
            with patch("aiskillsync.sync.is_windows_destination", return_value=True):
                plan = build_sync_plan(load_config(config), ("all",), (), dry_run=False)
                self.assertEqual(plan.copies[0].action, "copy-create")
                apply_sync_plan(plan)
            self.assertFalse((dest / "x").is_symlink())
            self.assertEqual((dest / "x" / "SKILL.md").read_text(), "data")

    def test_failed_record_write_preserves_prior_copy_backup(self) -> None:
        from unittest.mock import patch
        from aiskillsync.config import load_config
        from aiskillsync.sync import build_sync_plan, apply_sync_plan
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); source = base / "repo" / "skills" / "x"; source.mkdir(parents=True)
            (source / "SKILL.md").write_text("old")
            dest = base / "dest"; config = base / "config.yaml"
            config.write_text(f"""bridges:
  - name: source
    path: {base / 'repo'}
ai_skill_paths:
  windows: {dest}
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [windows]
  copy_destinations: [windows]
""")
            cfg = load_config(config)
            apply_sync_plan(build_sync_plan(cfg, ("all",), (), dry_run=False))
            (source / "SKILL.md").write_text("new")
            plan = build_sync_plan(cfg, ("all",), (), dry_run=False)
            with patch("aiskillsync.managed_copies.atomic_write_text", side_effect=OSError("write failed")):
                with self.assertRaisesRegex(ValueError, "preserved backup"):
                    apply_sync_plan(plan)
            backups = list(dest.glob(".x.aiskillsync-backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "SKILL.md").read_text(), "old")
            self.assertEqual((dest / "x" / "SKILL.md").read_text(), "new")
            plan = build_sync_plan(cfg, ("all",), (), dry_run=False)
            self.assertTrue(plan.has_blockers)


    def test_lane_switch_ignores_unrelated_windows_copy_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in ("stable", "beta", "other"):
                skill = base / name / "skills" / ("other" if name == "other" else "x")
                skill.mkdir(parents=True); (skill / "SKILL.md").write_text(name)
            config = base / "config.yaml"
            config.write_text(f"""bridges:
  - name: stable
    path: {base / 'stable'}
  - name: beta
    path: {base / 'beta'}
  - name: other
    path: {base / 'other'}
lanes:
  policy:
    default: stable
    variants:
      stable: stable
      beta: beta
ai_skill_paths:
  linux: {base / 'linux'}
  windows: {base / 'windows'}
profile_sets:
  desktop:
    destinations: [linux]
    sources:
      - bridge: stable
      - bridge: beta
  unrelated:
    destinations: [windows]
    sources:
      - bridge: other
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [linux]
  copy_destinations: [windows]
""")
            old = base / "stable" / "skills" / "removed"; old.mkdir()
            (old / "SKILL.md").write_text("old")
            env = dict(os.environ, HOME=str(base), PYTHONPATH=str(ROOT))
            initial = subprocess.run([sys.executable, "-m", "aiskillsync", "--config", str(config),
                                      "sync", "--profile", "desktop"], cwd=ROOT, env=env,
                                     text=True, capture_output=True)
            self.assertEqual(initial.returncode, 0, initial.stdout + initial.stderr)
            other = subprocess.run([sys.executable, "-m", "aiskillsync", "--config", str(config),
                                    "sync", "--profile", "unrelated"], cwd=ROOT, env=env,
                                   text=True, capture_output=True)
            self.assertEqual(other.returncode, 0, other.stdout + other.stderr)
            (base / "other" / "skills" / "other" / "SKILL.md").write_text("changed")
            result = subprocess.run([sys.executable, "-m", "aiskillsync", "--config", str(config), "switch", "beta"],
                                    cwd=ROOT, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((base / "linux" / "removed").exists())
            self.assertEqual((base / "linux" / "x").resolve(), base / "beta" / "skills" / "x")
            self.assertEqual((base / "windows" / "other" / "SKILL.md").read_text(), "other")

    def test_lane_switch_blocks_copy_profile_outside_default_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in ("stable", "beta"):
                skill = base / name / "skills" / "x"; skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(name)
            config = base / "config.yaml"
            config.write_text(f"""bridges:
  - name: stable
    path: {base / 'stable'}
  - name: beta
    path: {base / 'beta'}
lanes:
  policy:
    default: stable
    variants:
      stable: stable
      beta: beta
ai_skill_paths:
  linux: {base / 'linux'}
  windows: {base / 'windows'}
profile_sets:
  desktop:
    destinations: [linux, windows]
    sources:
      - bridge: stable
      - bridge: beta
sync:
  pull_before_sync: false
  clone_if_missing: false
  default_destinations: [linux]
  copy_destinations: [windows]
""")
            result = subprocess.run([sys.executable, "-m", "aiskillsync", "--config", str(config), "switch", "beta"],
                                    cwd=ROOT, env=dict(os.environ, HOME=str(base), PYTHONPATH=str(ROOT)),
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("lane switch is unsupported for copy destination", result.stdout + result.stderr)
            self.assertFalse((base / "windows").exists())


    def test_snapshot_does_not_ignore_unreadable_directories(self) -> None:
        from unittest.mock import patch
        from aiskillsync.managed_copies import snapshot
        with tempfile.TemporaryDirectory() as tmp:
            with patch("os.scandir", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    snapshot(Path(tmp))
