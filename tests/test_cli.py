from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliSmokeTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "aiskillsync", *args],
            cwd=ROOT,
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

        self.assertEqual(default_result.returncode, 0, default_result.stderr)
        self.assertIn("bridges:", default_result.stdout)
        self.assertIn("ai_skill_paths:", default_result.stdout)
        self.assertEqual(strict_result.returncode, 2)
        self.assertIn("config does not exist", strict_result.stderr)

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


if __name__ == "__main__":
    unittest.main()
