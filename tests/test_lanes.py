from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from aiskillsync.cli import main
from aiskillsync.config import ConfigError, config_from_mapping, config_to_text, load_config, parse_simple_yaml


class LaneCliTests(unittest.TestCase):
    def write_skill(self, root: Path, name: str) -> None:
        path = root / "skills" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test skill\n---\n",
            encoding="utf-8",
        )

    def init_repo(self, path: Path, branch: str) -> None:
        launcher = path / "scripts" / "bbh"
        launcher.parent.mkdir(exist_ok=True)
        launcher.write_text(f"#!/bin/sh\necho {branch}\n", encoding="utf-8")
        launcher.chmod(0o755)
        subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(path), "-c", "user.email=test@example.test", "-c", "user.name=Test", "commit", "-qm", "initial"],
            check=True,
        )

    def write_config(self, base: Path) -> Path:
        config = base / "config.yaml"
        config.write_text(
            f"""bridges:
  - name: stable
    path: {base / 'stable'}
    skills_path: skills
    branch: main
    enabled: true
  - name: beta
    path: {base / 'beta'}
    skills_path: skills
    branch: beta
    enabled: true
lanes:
  policies:
    default: stable
    variants:
      stable: stable
      beta: beta
bbh_launcher_path: {base / 'bin' / 'bbh'}
bbh_launcher_lane_group: policies
ai_skill_paths:
  test: {base / 'destination'}
sync:
  mode: symlink
  pull_before_sync: false
  clone_if_missing: false
  default_destinations:
    - test
""",
            encoding="utf-8",
        )
        return config

    def test_doctor_uses_active_lane_without_duplicate_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            self.write_skill(base / "stable", "shared")
            self.write_skill(base / "beta", "shared")
            self.init_repo(base / "stable", "main")
            self.init_repo(base / "beta", "beta")
            (base / "destination").mkdir()
            config = self.write_config(base)

            self.assertEqual(main(["--config", str(config), "doctor"]), 0)

    def test_switch_repoints_shared_and_prunes_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            self.write_skill(base / "stable", "shared")
            self.write_skill(base / "stable", "removed")
            self.write_skill(base / "beta", "shared")
            self.init_repo(base / "stable", "main")
            self.init_repo(base / "beta", "beta")
            (base / "destination").mkdir()
            config = self.write_config(base)

            self.assertEqual(main(["--config", str(config), "sync", "all"]), 0)
            self.assertEqual((base / "destination" / "shared").resolve(), base / "stable" / "skills" / "shared")
            self.assertTrue((base / "destination" / "removed").is_symlink())

            self.assertEqual(main(["--config", str(config), "switch", "beta"]), 0)
            self.assertEqual((base / "destination" / "shared").resolve(), base / "beta" / "skills" / "shared")
            self.assertFalse((base / "destination" / "removed").exists())
            self.assertEqual((base / "bin" / "bbh").resolve(), base / "beta" / "scripts" / "bbh")
            state = json.loads((base / "active-stack.json").read_text(encoding="utf-8"))
            self.assertEqual(state["lanes"], {"policies": "beta"})

    def test_switch_writes_resolved_runtime_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            self.write_skill(base / "stable", "shared")
            self.write_skill(base / "beta", "shared")
            self.init_repo(base / "stable", "main")
            self.init_repo(base / "beta", "beta")
            (base / "destination").mkdir()
            config = self.write_config(base)

            self.assertEqual(main(["--config", str(config), "switch", "beta"]), 0)

            context = json.loads((base / "active-stack.json").read_text(encoding="utf-8"))
            self.assertEqual(context["version"], 1)
            self.assertEqual(context["lanes"], {"policies": "beta"})
            self.assertEqual(context["projects"]["policies"]["root"], str((base / "beta").resolve()))
            self.assertEqual(context["projects"]["policies"]["branch"], "beta")
            self.assertTrue(context["projects"]["policies"]["revision"])

    def test_switching_unrelated_lane_does_not_repoint_bbh_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for name, branch, skill in (
                ("bbh-stable", "main", "bbh"),
                ("bbh-beta", "beta", "bbh"),
                ("policies-stable", "main", "policies"),
                ("policies-beta", "beta", "policies"),
            ):
                self.write_skill(base / name, skill)
                self.init_repo(base / name, branch)
            (base / "destination").mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: bbh-stable
    path: {base / 'bbh-stable'}
    skills_path: skills
    branch: main
  - name: bbh-beta
    path: {base / 'bbh-beta'}
    skills_path: skills
    branch: beta
  - name: policies-stable
    path: {base / 'policies-stable'}
    skills_path: skills
    branch: main
  - name: policies-beta
    path: {base / 'policies-beta'}
    skills_path: skills
    branch: beta
lanes:
  bbh:
    default: stable
    variants:
      stable: bbh-stable
      beta: bbh-beta
  policies:
    default: stable
    variants:
      stable: policies-stable
      beta: policies-beta
bbh_launcher_path: {base / 'bin' / 'bbh'}
bbh_launcher_lane_group: bbh
ai_skill_paths:
  test: {base / 'destination'}
sync:
  mode: symlink
  pull_before_sync: false
  clone_if_missing: false
  default_destinations:
    - test
""",
                encoding="utf-8",
            )

            self.assertEqual(main(["--config", str(config), "switch", "policies", "beta"]), 0)

            self.assertFalse((base / "bin" / "bbh").exists())
            context = json.loads((base / "active-stack.json").read_text(encoding="utf-8"))
            self.assertEqual(context["lanes"], {"bbh": "stable", "policies": "beta"})

    def test_serialized_config_preserves_bbh_launcher_lane_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            config_path = self.write_config(base)

            restored = config_from_mapping(
                parse_simple_yaml(config_to_text(load_config(config_path))), base / "restored.yaml"
            )

            self.assertEqual(restored.bbh_launcher_path, base / "bin" / "bbh")
            self.assertEqual(restored.bbh_launcher_lane_group, "policies")

    def test_switch_context_write_failure_does_not_persist_lane_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            self.write_skill(base / "stable", "shared")
            self.write_skill(base / "beta", "shared")
            self.init_repo(base / "stable", "main")
            self.init_repo(base / "beta", "beta")
            (base / "destination").mkdir()
            config = self.write_config(base)

            with patch("aiskillsync.lanes.os.replace", side_effect=OSError("simulated context write failure")):
                self.assertEqual(main(["--config", str(config), "switch", "beta"]), 1)

            self.assertFalse((base / "active-stack.json").exists())
            self.assertFalse((base / "lanes-state.json").exists())
            self.assertEqual((base / "destination" / "shared").resolve(), base / "stable" / "skills" / "shared")

    def test_loads_legacy_lane_state_when_no_runtime_context_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            config = self.write_config(base)
            (base / "lanes-state.json").write_text(
                '{"version": 1, "active": {"policies": "beta"}}\n', encoding="utf-8"
            )
            self.assertEqual(load_config(config).lanes[0].name, "policies")
            from aiskillsync.lanes import load_active_lanes
            self.assertEqual(load_active_lanes(load_config(config)), {"policies": "beta"})

    def test_branch_mismatch_blocks_sync_before_destination_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            self.write_skill(base / "stable", "shared")
            self.write_skill(base / "beta", "shared")
            self.init_repo(base / "stable", "main")
            self.init_repo(base / "beta", "wrong")
            (base / "destination").mkdir()
            config = self.write_config(base)

            self.assertEqual(main(["--config", str(config), "switch", "beta"]), 1)
            self.assertFalse((base / "destination" / "shared").exists())
            self.assertFalse((base / "lanes-state.json").exists())

    def test_invalid_lane_bridge_is_rejected_by_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.yaml"
            config.write_text(
                "bridges: []\nlanes:\n  policies:\n    default: stable\n    variants:\n      stable: missing\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "unknown bridge"):
                load_config(config)


if __name__ == "__main__":
    unittest.main()
