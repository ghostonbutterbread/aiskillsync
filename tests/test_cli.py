from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from aiskillsync.config import DEFAULT_CONFIG_TEXT


ROOT = Path(__file__).resolve().parents[1]


class CliSmokeTests(unittest.TestCase):
    def run_cli(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        child_env = os.environ.copy()
        if env:
            child_env.update(env)
        return subprocess.run(
            [sys.executable, "-m", "aiskillsync", *args],
            cwd=ROOT,
            env=child_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def write_skill(self, root: Path, name: str, body: str = "ok") -> Path:
        skill_dir = root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
        return skill_dir

    def write_config(self, path: Path, bridge: Path, codex: Path, claude: Path) -> None:
        path.write_text(
            f"""bridges:
  - name: local-bridge
    repo: https://example.invalid/local-bridge.git
    path: {bridge}
    skills_path: skills
    branch: main
    enabled: true

ai_skill_paths:
  codex: {codex}
  claude: {claude}

sync:
  mode: symlink
  pull_before_sync: false
  clone_if_missing: false
  default_destinations:
    - codex
    - claude
""",
            encoding="utf-8",
        )

    def write_phase3_config(
        self,
        path: Path,
        bridges: list[tuple[str, Path]],
        destinations: dict[str, Path],
        *,
        mode: str = "symlink",
        default_destinations: tuple[str, ...] = ("codex",),
    ) -> None:
        bridge_lines = ["bridges:"]
        for name, bridge_path in bridges:
            bridge_lines.extend(
                [
                    f"  - name: {name}",
                    f"    path: {bridge_path}",
                    "    skills_path: skills",
                    "    enabled: true",
                ]
            )
        dest_lines = ["ai_skill_paths:"]
        for name, dest_path in destinations.items():
            dest_lines.append(f"  {name}: {dest_path}")
        default_lines = ["  default_destinations:"]
        for name in default_destinations:
            default_lines.append(f"    - {name}")
        path.write_text(
            "\n".join(
                [
                    *bridge_lines,
                    "",
                    *dest_lines,
                    "",
                    "sync:",
                    f"  mode: {mode}",
                    "  pull_before_sync: false",
                    "  clone_if_missing: false",
                    *default_lines,
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def test_init_dry_run(self) -> None:
        result = self.run_cli("init", "--dry-run")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would write config", result.stdout)
        self.assertIn("bridges:", result.stdout)
        self.assertIn("ai_skill_paths:", result.stdout)

    def test_init_does_not_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"

            first = self.run_cli("--config", str(config), "init")
            config.write_text("sentinel: true\n", encoding="utf-8")
            second = self.run_cli("--config", str(config), "init")

            content = config.read_text(encoding="utf-8")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 1, second.stderr)
        self.assertIn("config already exists", second.stderr)
        self.assertEqual(content, "sentinel: true\n")

    def test_config_default_prints_template_without_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_config = Path(tmp) / "missing.yaml"

            default_result = self.run_cli("--config", str(missing_config), "config", "--default")
            strict_result = self.run_cli("--config", str(missing_config), "config")
            strict_list_result = self.run_cli("--config", str(missing_config), "list")

        self.assertEqual(default_result.returncode, 0, default_result.stderr)
        self.assertIn("bridges:", default_result.stdout)
        self.assertIn("ai_skill_paths:", default_result.stdout)
        self.assertEqual(strict_result.returncode, 2)
        self.assertIn("config does not exist", strict_result.stderr)
        self.assertEqual(strict_list_result.returncode, 2)
        self.assertIn("config does not exist", strict_list_result.stderr)

    def test_default_config_auto_created_for_first_run_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            config = home / ".config" / "aiskillsync" / "config.yaml"

            result = self.run_cli("config", env={"HOME": str(home)})
            content = config.read_text(encoding="utf-8") if config.exists() else ""

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Config: {config}", result.stdout)
        self.assertEqual(content, DEFAULT_CONFIG_TEXT)

    def test_config_default_does_not_create_default_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            config = home / ".config" / "aiskillsync" / "config.yaml"

            result = self.run_cli("config", "--default", env={"HOME": str(home)})
            exists = config.exists()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bridges:", result.stdout)
        self.assertFalse(exists)

    def test_doctor_auto_creates_default_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            config = home / ".config" / "aiskillsync" / "config.yaml"

            result = self.run_cli("doctor", env={"HOME": str(home)})
            exists = config.exists()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"OK config exists: {config}", result.stdout)
        self.assertIn("OK config parses", result.stdout)
        self.assertTrue(exists)

    def test_config_list_and_doctor_classify_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "bridge"
            skills = bridge / "skills"
            codex = base / "codex"
            claude = base / "claude"
            codex.mkdir()
            claude.mkdir()
            self.write_skill(skills, "appmap", "appmap")
            brainstorm = self.write_skill(skills, "brainstorm-spec", "brainstorm")
            (claude / "appmap").mkdir()
            (claude / "appmap" / "SKILL.md").write_text("copied", encoding="utf-8")
            (codex / "brainstorm-spec").symlink_to(brainstorm, target_is_directory=True)
            config = base / "config.yaml"
            self.write_config(config, bridge, codex, claude)

            config_result = self.run_cli("--config", str(config), "config")
            list_result = self.run_cli("--config", str(config), "list")
            doctor_result = self.run_cli("--config", str(config), "doctor")

        self.assertEqual(config_result.returncode, 0, config_result.stderr)
        self.assertIn("local-bridge", config_result.stdout)
        self.assertEqual(list_result.returncode, 0, list_result.stderr)
        self.assertIn("appmap", list_result.stdout)
        self.assertIn("brainstorm-spec", list_result.stdout)
        self.assertIn("directory-copy=1", list_result.stdout)
        self.assertEqual(doctor_result.returncode, 0, doctor_result.stdout + doctor_result.stderr)
        self.assertIn("appmap: missing", doctor_result.stdout)
        self.assertIn("appmap: regular directory/copy", doctor_result.stdout)
        self.assertIn("brainstorm-spec: already linked to correct source", doctor_result.stdout)

    def test_doctor_reports_duplicate_enabled_skill_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge_a = base / "bridge-a"
            bridge_b = base / "bridge-b"
            dest = base / "codex"
            dest.mkdir()
            self.write_skill(bridge_a / "skills", "shared")
            self.write_skill(bridge_b / "skills", "shared")
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: a
    path: {bridge_a}
    skills_path: skills
    enabled: true
  - name: b
    path: {bridge_b}
    skills_path: skills
    enabled: true

ai_skill_paths:
  codex: {dest}

sync:
  default_destinations:
    - codex
""",
                encoding="utf-8",
            )

            result = self.run_cli("--config", str(config), "doctor")

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("FAIL duplicate skill name shared", result.stdout)

    def test_doctor_fails_when_skills_path_missing_under_existing_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "bridge"
            dest = base / "codex"
            bridge.mkdir()
            dest.mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: local-bridge
    repo: https://example.invalid/local-bridge.git
    path: {bridge}
    skills_path: skills
    enabled: true

ai_skill_paths:
  codex: {dest}

sync:
  default_destinations:
    - codex
""",
                encoding="utf-8",
            )

            result = self.run_cli("--config", str(config), "doctor")

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("FAIL bridge local-bridge: local skills path missing", result.stdout)
        self.assertNotIn("repo is cloneable", result.stdout)

    def test_doctor_skips_disabled_bridge_skill_dir_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "bridge"
            bad_skill = bridge / "skills" / "bad-skill"
            dest = base / "codex"
            bad_skill.mkdir(parents=True)
            dest.mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: disabled-bridge
    path: {bridge}
    skills_path: skills
    enabled: false

ai_skill_paths:
  codex: {dest}

sync:
  default_destinations:
    - codex
""",
                encoding="utf-8",
            )

            result = self.run_cli("--config", str(config), "doctor")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("FAIL", result.stdout)
        self.assertIn("SKIP bridge disabled-bridge: disabled", result.stdout)
        self.assertIn("disabled skill dirs missing SKILL.md", result.stdout)

    def test_sync_selects_bridge_by_name_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge_a = base / "bridge-a"
            bridge_b = base / "bridge-b"
            codex = base / "codex"
            codex.mkdir()
            self.write_skill(bridge_a / "skills", "alpha")
            self.write_skill(bridge_b / "skills", "beta")
            config = base / "config.yaml"
            self.write_phase3_config(
                config,
                [("first", bridge_a), ("second", bridge_b)],
                {"codex": codex},
            )

            by_index = self.run_cli("--config", str(config), "sync", "2", "--dest", "codex")
            by_name = self.run_cli("--config", str(config), "sync", "first", "--dest", "codex")

        self.assertEqual(by_index.returncode, 0, by_index.stdout + by_index.stderr)
        self.assertIn("Bridges: second", by_index.stdout)
        self.assertIn("codex:beta", by_index.stdout)
        self.assertNotIn("codex:alpha", by_index.stdout)
        self.assertEqual(by_name.returncode, 0, by_name.stdout + by_name.stderr)
        self.assertIn("Bridges: first", by_name.stdout)
        self.assertIn("codex:alpha", by_name.stdout)
        self.assertNotIn("codex:beta", by_name.stdout)

    def test_sync_dry_run_does_not_mutate_and_dest_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "bridge"
            codex = base / "codex"
            claude = base / "claude"
            ghost = base / "ghost"
            codex.mkdir()
            claude.mkdir()
            ghost.mkdir()
            self.write_skill(bridge / "skills", "only-skill")
            config = base / "config.yaml"
            self.write_phase3_config(
                config,
                [("local", bridge)],
                {"codex": codex, "claude": claude, "ghost": ghost},
                default_destinations=("codex", "claude"),
            )

            result = self.run_cli("--config", str(config), "sync", "all", "--dest", "codex")
            codex_exists = (codex / "only-skill").exists()
            claude_exists = (claude / "only-skill").exists()
            ghost_exists = (ghost / "only-skill").exists()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Sync plan (dry-run)", result.stdout)
        self.assertIn("LINK codex:only-skill", result.stdout)
        self.assertNotIn("claude:only-skill", result.stdout)
        self.assertNotIn("ghost:only-skill", result.stdout)
        self.assertFalse(codex_exists)
        self.assertFalse(claude_exists)
        self.assertFalse(ghost_exists)

    def test_sync_apply_creates_only_missing_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "bridge"
            codex = base / "codex"
            claude = base / "claude"
            codex.mkdir()
            claude.mkdir()
            skill = self.write_skill(bridge / "skills", "sync-me")
            (claude / "sync-me").symlink_to(skill, target_is_directory=True)
            config = base / "config.yaml"
            self.write_phase3_config(
                config,
                [("local", bridge)],
                {"codex": codex, "claude": claude},
                default_destinations=("codex", "claude"),
            )

            result = self.run_cli("--config", str(config), "sync", "all", "--apply")
            codex_link = codex / "sync-me"
            claude_link = claude / "sync-me"

            codex_target = codex_link.resolve() if codex_link.is_symlink() else None
            claude_target = claude_link.resolve() if claude_link.is_symlink() else None

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LINK codex:sync-me", result.stdout)
        self.assertIn("SKIP claude:sync-me", result.stdout)
        self.assertIn("Created symlinks:", result.stdout)
        self.assertEqual(codex_target, skill.resolve())
        self.assertEqual(claude_target, skill.resolve())

    def test_sync_conflict_blocks_apply_and_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "bridge"
            codex = base / "codex"
            claude = base / "claude"
            codex.mkdir()
            claude.mkdir()
            self.write_skill(bridge / "skills", "blocked")
            (codex / "blocked").mkdir()
            config = base / "config.yaml"
            self.write_phase3_config(
                config,
                [("local", bridge)],
                {"codex": codex, "claude": claude},
                default_destinations=("codex", "claude"),
            )

            result = self.run_cli("--config", str(config), "sync", "all", "--apply")
            claude_exists = (claude / "blocked").exists()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("CONFLICT codex:blocked", result.stdout)
        self.assertIn("Apply blocked", result.stdout)
        self.assertFalse(claude_exists)

    def test_sync_duplicate_destination_roots_block_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "bridge"
            shared = base / "shared-dest"
            shared.mkdir()
            self.write_skill(bridge / "skills", "dupe-root")
            config = base / "config.yaml"
            self.write_phase3_config(
                config,
                [("local", bridge)],
                {"codex": shared, "mirror": shared},
                default_destinations=("codex", "mirror"),
            )

            result = self.run_cli("--config", str(config), "sync", "all", "--apply")
            created = (shared / "dupe-root").exists()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ERROR selected destinations share root path", result.stdout)
        self.assertIn("codex, mirror", result.stdout)
        self.assertIn("Apply blocked", result.stdout)
        self.assertFalse(created)

    def test_sync_missing_cloneable_bridge_blocks_apply_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            existing_bridge = base / "existing-bridge"
            missing_bridge = base / "missing-bridge"
            codex = base / "codex"
            codex.mkdir()
            self.write_skill(existing_bridge / "skills", "present-skill")
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: existing
    path: {existing_bridge}
    skills_path: skills
    enabled: true
  - name: cloneable
    repo: https://example.invalid/cloneable.git
    path: {missing_bridge}
    skills_path: skills
    enabled: true

ai_skill_paths:
  codex: {codex}

sync:
  mode: symlink
  pull_before_sync: false
  clone_if_missing: true
  default_destinations:
    - codex
""",
                encoding="utf-8",
            )

            result = self.run_cli("--config", str(config), "sync", "all", "--apply")
            created = (codex / "present-skill").exists()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("PLAN bridge cloneable: clone-if-missing is configured", result.stdout)
        self.assertIn("ERROR bridge cloneable: local path missing", result.stdout)
        self.assertIn("Apply blocked", result.stdout)
        self.assertFalse(created)

    def test_sync_destination_root_ancestor_conflict_blocks_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "bridge"
            good = base / "good"
            blocker = base / "blocker"
            blocker.write_text("not a directory", encoding="utf-8")
            self.write_skill(bridge / "skills", "ancestor-conflict")
            config = base / "config.yaml"
            self.write_phase3_config(
                config,
                [("local", bridge)],
                {"bad": blocker / "child", "good": good},
                default_destinations=("bad", "good"),
            )

            result = self.run_cli("--config", str(config), "sync", "all", "--apply")
            good_created = (good / "ancestor-conflict").exists()
            blocker_is_file = blocker.is_file()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ERROR destination bad: root path has non-directory ancestor", result.stdout)
        self.assertIn(str(blocker), result.stdout)
        self.assertIn("Apply blocked", result.stdout)
        self.assertFalse(good_created)
        self.assertTrue(blocker_is_file)

    def test_sync_unsupported_mode_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "bridge"
            codex = base / "codex"
            codex.mkdir()
            self.write_skill(bridge / "skills", "skill")
            config = base / "config.yaml"
            self.write_phase3_config(
                config,
                [("local", bridge)],
                {"codex": codex},
                mode="copy",
            )

            result = self.run_cli("--config", str(config), "sync", "all")

        self.assertEqual(result.returncode, 2)
        self.assertIn("unsupported sync.mode", result.stderr)


if __name__ == "__main__":
    unittest.main()
