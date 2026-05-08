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

    def write_fake_git(self, root: Path) -> Path:
        bin_dir = root / "bin"
        bin_dir.mkdir()
        git = bin_dir / "git"
        git.write_text(
            """#!/usr/bin/env python3
import os
import pathlib
import sys

args = sys.argv[1:]
log = os.environ.get("AISKILLSYNC_FAKE_GIT_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(" ".join(args) + "\\n")

if args and args[0] == "clone":
    exit_code = int(os.environ.get("AISKILLSYNC_FAKE_GIT_CLONE_EXIT", "0"))
    if exit_code:
        print("fake clone failed", file=sys.stderr)
        sys.exit(exit_code)
    target = pathlib.Path(args[-1])
    skill = os.environ.get("AISKILLSYNC_FAKE_GIT_CLONE_SKILL", "cloned-skill")
    (target / ".git").mkdir(parents=True)
    skill_dir = target / "skills" / skill
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("cloned", encoding="utf-8")
    sys.exit(0)

if len(args) == 4 and args[0] == "-C" and args[2:] == ["pull", "--ff-only"]:
    exit_code = int(os.environ.get("AISKILLSYNC_FAKE_GIT_PULL_EXIT", "0"))
    if exit_code:
        print("fake pull failed", file=sys.stderr)
    sys.exit(exit_code)

if len(args) == 4 and args[0] == "-C" and args[2:] == ["rev-parse", "--is-inside-work-tree"]:
    print("true")
    sys.exit(0)

print("unsupported fake git invocation: " + " ".join(args), file=sys.stderr)
sys.exit(9)
""",
            encoding="utf-8",
        )
        git.chmod(0o755)
        return bin_dir

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
        self.assertIn("codex: ~/.codex/skills", result.stdout)

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

    def test_pyproject_exposes_only_aiskillsync_console_script(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("aiskillsync = \"aiskillsync.cli:main\"", text)
        self.assertNotIn("migrate_aiskillsync_personal", text)

    def test_repo_add_uses_repo_dir_and_sync_apply_clones_configured_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake_git = self.write_fake_git(base)
            repo_dir = base / "repos"
            codex = base / "codex"
            codex.mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""repo_dir: {repo_dir}

bridges: []

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

            add_result = self.run_cli(
                "--config",
                str(config),
                "repo",
                "add",
                "https://example.invalid/toolkit.git",
                "--branch",
                "main",
            )
            content_after_add = config.read_text(encoding="utf-8")
            sync_result = self.run_cli(
                "--config",
                str(config),
                "sync",
                "codex",
                "--repo",
                "toolkit",
                "--apply",
                env={
                    "PATH": f"{fake_git}:{os.environ['PATH']}",
                    "AISKILLSYNC_FAKE_GIT_CLONE_SKILL": "from-added",
                },
            )
            link = codex / "from-added"
            target = link.resolve() if link.is_symlink() else None

        self.assertEqual(add_result.returncode, 0, add_result.stdout + add_result.stderr)
        self.assertIn("Added repo toolkit", add_result.stdout)
        self.assertIn(f"path: {repo_dir / 'toolkit'}", content_after_add)
        self.assertIn("branch: main", content_after_add)
        self.assertEqual(sync_result.returncode, 0, sync_result.stdout + sync_result.stderr)
        self.assertIn(f"CLONE repo toolkit: git clone --branch main", sync_result.stdout)
        self.assertEqual(target, (repo_dir / "toolkit" / "skills" / "from-added").resolve())

    def test_repo_add_without_repo_dir_defaults_to_config_aiskillsync_repos_not_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            home.mkdir()
            codex = base / "codex"
            codex.mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""bridges: []

ai_skill_paths:
  codex: {codex}

sync:
  default_destinations:
    - codex
""",
                encoding="utf-8",
            )

            result = self.run_cli(
                "--config",
                str(config),
                "repo",
                "add",
                "https://example.invalid/defaulted.git",
                env={"HOME": str(home)},
            )
            content = config.read_text(encoding="utf-8")

        default_repo_dir = home / ".config" / "aiskillsync" / "repos"
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("repo_dir: ~/.config/aiskillsync/repos", content)
        self.assertIn("path: ~/.config/aiskillsync/repos/defaulted", content)
        self.assertNotIn(f"path: {default_repo_dir / 'defaulted'}", content)
        self.assertNotIn("path: defaulted", content)

    def test_repo_add_preserves_comments_unknown_keys_and_repo_dir_literal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            home.mkdir()
            codex = base / "codex"
            codex.mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""# top comment
custom_top:
  keep: true

repo_dir: ~/skill-repos # keep repo dir literal

bridges: [] # managed repos

# destination comment
ai_skill_paths:
  codex: {codex}

unknown_after: keep-me

sync:
  default_destinations:
    - codex
""",
                encoding="utf-8",
            )

            result = self.run_cli(
                "--config",
                str(config),
                "repo",
                "add",
                "https://example.invalid/toolkit.git",
                env={"HOME": str(home)},
            )
            content = config.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("# top comment", content)
        self.assertIn("custom_top:", content)
        self.assertIn("unknown_after: keep-me", content)
        self.assertIn("repo_dir: ~/skill-repos # keep repo dir literal", content)
        self.assertIn("bridges: # managed repos", content)
        self.assertIn("path: ~/skill-repos/toolkit", content)
        self.assertNotIn(str(home / "skill-repos" / "toolkit"), content)
        self.assertLess(content.index("custom_top:"), content.index("repo_dir:"))
        self.assertLess(content.index("unknown_after:"), content.index("sync:"))

    def test_repo_add_creates_bridges_block_without_rewriting_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            codex = base / "codex"
            codex.mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""# no bridges yet
ai_skill_paths:
  codex: {codex}

custom_tail: preserved
""",
                encoding="utf-8",
            )

            result = self.run_cli(
                "--config",
                str(config),
                "repo",
                "add",
                "https://example.invalid/new-one.git",
            )
            content = config.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("# no bridges yet", content)
        self.assertIn("repo_dir: ~/.config/aiskillsync/repos", content)
        self.assertIn("custom_tail: preserved", content)
        self.assertIn("\nbridges:\n  - name: new-one\n", content)
        self.assertIn("path: ~/.config/aiskillsync/repos/new-one", content)

    def test_repo_add_url_explicit_positional_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo_dir = base / "repos"
            checkout = base / "custom" / "toolkit"
            codex = base / "codex"
            codex.mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""repo_dir: {repo_dir}

bridges: []

ai_skill_paths:
  codex: {codex}

sync:
  default_destinations:
    - codex
""",
                encoding="utf-8",
            )

            result = self.run_cli(
                "--config",
                str(config),
                "repo",
                "add",
                "https://example.invalid/toolkit.git",
                str(checkout),
            )
            content = config.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Added repo toolkit", result.stdout)
        self.assertIn(f"path: {checkout}", content)
        self.assertNotIn(f"path: {repo_dir / 'toolkit'}", content)

    def test_top_level_add_alias_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            checkout = base / "custom" / "alias"
            codex = base / "codex"
            codex.mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""bridges: []

ai_skill_paths:
  codex: {codex}

sync:
  default_destinations:
    - codex
""",
                encoding="utf-8",
            )

            result = self.run_cli(
                "--config",
                str(config),
                "add",
                "https://example.invalid/alias.git",
                str(checkout),
            )
            content = config.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Added repo alias", result.stdout)
        self.assertIn(f"path: {checkout}", content)

    def test_repo_add_local_path_uses_path_name_and_does_not_clone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake_git = self.write_fake_git(base)
            log = base / "git.log"
            local_repo = base / "local-tools"
            codex = base / "codex"
            codex.mkdir()
            self.write_skill(local_repo / "skills", "local-skill")
            config = base / "config.yaml"
            config.write_text(
                f"""repo_dir: {base / 'repos'}

bridges: []

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

            add_result = self.run_cli(
                "--config",
                str(config),
                "repo",
                "add",
                str(local_repo),
                env={
                    "PATH": f"{fake_git}:{os.environ['PATH']}",
                    "AISKILLSYNC_FAKE_GIT_LOG": str(log),
                },
            )
            content = config.read_text(encoding="utf-8")
            sync_result = self.run_cli(
                "--config",
                str(config),
                "sync",
                "codex",
                "--repo",
                "local-tools",
                "--apply",
                env={
                    "PATH": f"{fake_git}:{os.environ['PATH']}",
                    "AISKILLSYNC_FAKE_GIT_LOG": str(log),
                },
            )
            link = codex / "local-skill"
            target = link.resolve() if link.is_symlink() else None
            git_was_called = log.exists()

        self.assertEqual(add_result.returncode, 0, add_result.stdout + add_result.stderr)
        self.assertIn("Added repo local-tools (enabled): local path", add_result.stdout)
        self.assertIn("  - name: local-tools", content)
        self.assertNotIn("repo: ", content)
        self.assertIn(f"path: {local_repo}", content)
        self.assertEqual(sync_result.returncode, 0, sync_result.stdout + sync_result.stderr)
        self.assertIn("LINK codex:local-skill", sync_result.stdout)
        self.assertEqual(target, (local_repo / "skills" / "local-skill").resolve())
        self.assertFalse(git_was_called)

    def test_repo_add_path_alias_still_sets_checkout_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            checkout = base / "legacy-path"
            codex = base / "codex"
            codex.mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""bridges: []

ai_skill_paths:
  codex: {codex}

sync:
  default_destinations:
    - codex
""",
                encoding="utf-8",
            )

            result = self.run_cli(
                "--config",
                str(config),
                "repo",
                "add",
                "https://example.invalid/legacy.git",
                "--path",
                str(checkout),
            )
            content = config.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"path: {checkout}", content)

    def test_repo_remove_updates_config_without_deleting_repo_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo_dir = base / "repos"
            checkout = repo_dir / "toolkit"
            checkout.mkdir(parents=True)
            sentinel = checkout / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")
            codex = base / "codex"
            codex.mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""repo_dir: {repo_dir}

bridges:
  - name: toolkit
    repo: https://example.invalid/toolkit.git
    path: {checkout}
    skills_path: skills
    enabled: true

ai_skill_paths:
  codex: {codex}

sync:
  default_destinations:
    - codex
""",
                encoding="utf-8",
            )

            result = self.run_cli("--config", str(config), "repo", "remove", "toolkit")
            content = config.read_text(encoding="utf-8")
            sentinel_exists = sentinel.exists()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Removed repo toolkit from config", result.stdout)
        self.assertIn("Left local path untouched", result.stdout)
        self.assertIn("bridges: []", content)
        self.assertTrue(sentinel_exists)

    def test_repo_remove_by_url_then_name_updates_config_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "first"
            second = base / "second"
            first.mkdir()
            second.mkdir()
            codex = base / "codex"
            codex.mkdir()
            first_url = "https://example.invalid/first.git"
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: first
    repo: {first_url}
    path: {first}
    skills_path: skills
    enabled: true
  - name: second
    repo: https://example.invalid/second.git
    path: {second}
    skills_path: skills
    enabled: true

ai_skill_paths:
  codex: {codex}

sync:
  default_destinations:
    - codex
""",
                encoding="utf-8",
            )

            by_url = self.run_cli("--config", str(config), "repo", "remove", first_url)
            content_after_url = config.read_text(encoding="utf-8")
            first_still_exists = first.exists()
            by_name = self.run_cli("--config", str(config), "repo", "remove", "second")
            content_after_name = config.read_text(encoding="utf-8")
            second_still_exists = second.exists()

        self.assertEqual(by_url.returncode, 0, by_url.stdout + by_url.stderr)
        self.assertIn("Removed repo first from config", by_url.stdout)
        self.assertNotIn("name: first", content_after_url)
        self.assertIn("name: second", content_after_url)
        self.assertEqual(by_name.returncode, 0, by_name.stdout + by_name.stderr)
        self.assertIn("Removed repo second from config", by_name.stdout)
        self.assertIn("bridges: []", content_after_name)
        self.assertTrue(first_still_exists)
        self.assertTrue(second_still_exists)

    def test_repo_remove_preserves_comments_unknown_keys_and_other_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "first"
            second = base / "second"
            first.mkdir()
            second.mkdir()
            codex = base / "codex"
            codex.mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""# preserve header
custom_top: yes

bridges:
  - name: first
    repo: https://example.invalid/first.git
    path: {first}
    skills_path: skills
    enabled: true
  - name: second
    repo: https://example.invalid/second.git
    path: {second}
    skills_path: skills
    enabled: true

# keep destination comment
ai_skill_paths:
  codex: {codex}

unknown_after: still-here
""",
                encoding="utf-8",
            )

            result = self.run_cli("--config", str(config), "repo", "remove", "first")
            content = config.read_text(encoding="utf-8")
            first_still_exists = first.exists()
            second_still_exists = second.exists()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("# preserve header", content)
        self.assertIn("custom_top: yes", content)
        self.assertIn("# keep destination comment", content)
        self.assertIn("unknown_after: still-here", content)
        self.assertNotIn("name: first", content)
        self.assertIn("name: second", content)
        self.assertNotIn("bridges: []", content)
        self.assertTrue(first_still_exists)
        self.assertTrue(second_still_exists)

    def test_repo_remove_ignores_nested_lists_when_selecting_repo_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = base / "first"
            second = base / "second"
            first.mkdir()
            second.mkdir()
            codex = base / "codex"
            codex.mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: first
    repo: https://example.invalid/first.git
    path: {first}
    skills_path: skills
    enabled: true
    labels:
      - keep-a
      - keep-b
  - name: second
    repo: https://example.invalid/second.git
    path: {second}
    skills_path: skills
    enabled: true

ai_skill_paths:
  codex: {codex}
""",
                encoding="utf-8",
            )

            result = self.run_cli("--config", str(config), "repo", "remove", "second")
            content = config.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("name: first", content)
        self.assertIn("labels:", content)
        self.assertIn("- keep-a", content)
        self.assertIn("- keep-b", content)
        self.assertNotIn("name: second", content)

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
        self.assertIn("FAIL repo local-bridge: local skills path missing", result.stdout)
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
        self.assertIn("SKIP repo disabled-bridge: disabled", result.stdout)
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
        self.assertIn("Repos: second", by_index.stdout)
        self.assertIn("codex:beta", by_index.stdout)
        self.assertNotIn("codex:alpha", by_index.stdout)
        self.assertEqual(by_name.returncode, 0, by_name.stdout + by_name.stderr)
        self.assertIn("Repos: first", by_name.stdout)
        self.assertIn("codex:alpha", by_name.stdout)
        self.assertNotIn("codex:beta", by_name.stdout)

    def test_sync_main_destination_group_selects_codex_and_claude(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "bridge"
            codex = base / "codex"
            claude = base / "claude"
            ghost = base / "ghost"
            codex.mkdir()
            claude.mkdir()
            ghost.mkdir()
            self.write_skill(bridge / "skills", "main-skill")
            config = base / "config.yaml"
            self.write_phase3_config(
                config,
                [("local", bridge)],
                {"codex": codex, "claude": claude, "ghost": ghost},
                default_destinations=("codex",),
            )

            result = self.run_cli("--config", str(config), "sync", "main")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Destinations: codex, claude", result.stdout)
        self.assertIn("LINK codex:main-skill", result.stdout)
        self.assertIn("LINK claude:main-skill", result.stdout)
        self.assertNotIn("ghost:main-skill", result.stdout)

    def test_sync_codex_destination_group_selects_codex_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "bridge"
            codex = base / "codex"
            claude = base / "claude"
            codex.mkdir()
            claude.mkdir()
            self.write_skill(bridge / "skills", "codex-skill")
            config = base / "config.yaml"
            self.write_phase3_config(
                config,
                [("local", bridge)],
                {"codex": codex, "claude": claude},
                default_destinations=("codex", "claude"),
            )

            result = self.run_cli("--config", str(config), "sync", "codex")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Destinations: codex", result.stdout)
        self.assertIn("LINK codex:codex-skill", result.stdout)
        self.assertNotIn("claude:codex-skill", result.stdout)

    def test_sync_openclaw_destination_group_aliases_ghost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "bridge"
            codex = base / "codex"
            ghost = base / "ghost"
            codex.mkdir()
            ghost.mkdir()
            self.write_skill(bridge / "skills", "ghost-skill")
            config = base / "config.yaml"
            self.write_phase3_config(
                config,
                [("local", bridge)],
                {"codex": codex, "ghost": ghost},
                default_destinations=("codex",),
            )

            result = self.run_cli("--config", str(config), "sync", "openclaw")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Destinations: ghost", result.stdout)
        self.assertIn("LINK ghost:ghost-skill", result.stdout)
        self.assertNotIn("codex:ghost-skill", result.stdout)

    def test_sync_repo_option_selects_configured_bridge_by_name(self) -> None:
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

            result = self.run_cli(
                "--config", str(config), "sync", "codex", "--repo", "second"
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Repos: second", result.stdout)
        self.assertIn("LINK codex:beta", result.stdout)
        self.assertNotIn("codex:alpha", result.stdout)

    def test_sync_repo_option_url_reuses_configured_bridge_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "bridge"
            codex = base / "codex"
            codex.mkdir()
            self.write_skill(bridge / "skills", "url-skill")
            repo_url = "https://example.invalid/configured.git"
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: configured
    repo: {repo_url}
    path: {bridge}
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

            result = self.run_cli(
                "--config", str(config), "sync", "codex", "--repo", repo_url
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Repos: configured", result.stdout)
        self.assertIn(f"LINK codex:url-skill missing ({codex / 'url-skill'})", result.stdout)
        self.assertNotIn("ADHOC repo", result.stdout)
        self.assertNotIn(".cache/aiskillsync", result.stdout)

    def test_sync_unconfigured_repo_url_uses_deterministic_repo_dir_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cache = base / "cache"
            repo_dir = base / "repos"
            codex = base / "codex"
            codex.mkdir()
            repo_url = "https://example.invalid/new-skills.git"
            config = base / "config.yaml"
            config.write_text(
                f"""repo_dir: {repo_dir}

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

            result = self.run_cli(
                "--config",
                str(config),
                "sync",
                "codex",
                "--repo",
                repo_url,
                env={"XDG_CACHE_HOME": str(cache)},
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ADHOC repo new-skills-", result.stdout)
        self.assertIn(str(repo_dir / "new-skills-"), result.stdout)
        self.assertNotIn(str(cache), result.stdout)
        self.assertIn("PLAN repo new-skills-", result.stdout)
        self.assertIn("No destination actions", result.stdout)

    def test_sync_all_legacy_syntax_uses_config_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = base / "bridge"
            codex = base / "codex"
            ghost = base / "ghost"
            codex.mkdir()
            ghost.mkdir()
            self.write_skill(bridge / "skills", "legacy-skill")
            config = base / "config.yaml"
            self.write_phase3_config(
                config,
                [("local", bridge)],
                {"codex": codex, "ghost": ghost},
                default_destinations=("codex",),
            )

            result = self.run_cli("--config", str(config), "sync", "all")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Repos: local", result.stdout)
        self.assertIn("Destinations: codex", result.stdout)
        self.assertIn("LINK codex:legacy-skill", result.stdout)
        self.assertNotIn("ghost:legacy-skill", result.stdout)

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

    def test_sync_dry_run_plans_clone_without_running_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake_git = self.write_fake_git(base)
            log = base / "git.log"
            missing_bridge = base / "missing-bridge"
            codex = base / "codex"
            codex.mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: cloneable
    repo: file:///tmp/fake-remote.git
    path: {missing_bridge}
    skills_path: skills
    branch: main
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

            result = self.run_cli(
                "--config",
                str(config),
                "sync",
                "all",
                env={
                    "PATH": f"{fake_git}:{os.environ['PATH']}",
                    "AISKILLSYNC_FAKE_GIT_LOG": str(log),
                },
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            f"PLAN repo cloneable: git clone --branch main file:///tmp/fake-remote.git {missing_bridge}",
            result.stdout,
        )
        self.assertIn("No destination actions", result.stdout)
        self.assertFalse(missing_bridge.exists())
        self.assertFalse(log.exists())

    def test_sync_apply_clones_missing_bridge_before_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake_git = self.write_fake_git(base)
            log = base / "git.log"
            bridge = base / "cloned-bridge"
            codex = base / "codex"
            codex.mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: cloneable
    repo: file:///tmp/fake-remote.git
    path: {bridge}
    skills_path: skills
    branch: main
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

            result = self.run_cli(
                "--config",
                str(config),
                "sync",
                "all",
                "--apply",
                env={
                    "PATH": f"{fake_git}:{os.environ['PATH']}",
                    "AISKILLSYNC_FAKE_GIT_LOG": str(log),
                    "AISKILLSYNC_FAKE_GIT_CLONE_SKILL": "from-clone",
                },
            )
            link = codex / "from-clone"
            target = link.resolve() if link.is_symlink() else None
            log_text = log.read_text(encoding="utf-8") if log.exists() else ""

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CLONE repo cloneable: git clone --branch main", result.stdout)
        self.assertIn("LINK codex:from-clone", result.stdout)
        self.assertIn("Created symlinks:", result.stdout)
        self.assertIn(f"clone --branch main file:///tmp/fake-remote.git {bridge}", log_text)
        self.assertEqual(target, (bridge / "skills" / "from-clone").resolve())

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

    def test_sync_clone_failure_blocks_apply_without_symlink_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake_git = self.write_fake_git(base)
            log = base / "git.log"
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

            result = self.run_cli(
                "--config",
                str(config),
                "sync",
                "all",
                "--apply",
                env={
                    "PATH": f"{fake_git}:{os.environ['PATH']}",
                    "AISKILLSYNC_FAKE_GIT_LOG": str(log),
                    "AISKILLSYNC_FAKE_GIT_CLONE_EXIT": "4",
                },
            )
            created = (codex / "present-skill").exists()
            log_text = log.read_text(encoding="utf-8") if log.exists() else ""

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("CLONE repo cloneable: git clone", result.stdout)
        self.assertIn("ERROR repo cloneable: git clone failed with exit 4", result.stdout)
        self.assertIn("Apply blocked", result.stdout)
        self.assertIn(f"clone https://example.invalid/cloneable.git {missing_bridge}", log_text)
        self.assertFalse(created)

    def test_sync_pull_failure_blocks_symlink_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake_git = self.write_fake_git(base)
            log = base / "git.log"
            bridge = base / "bridge"
            codex = base / "codex"
            codex.mkdir()
            self.write_skill(bridge / "skills", "needs-pull")
            (bridge / ".git").mkdir()
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: local
    path: {bridge}
    skills_path: skills
    enabled: true

ai_skill_paths:
  codex: {codex}

sync:
  mode: symlink
  pull_before_sync: true
  clone_if_missing: false
  default_destinations:
    - codex
""",
                encoding="utf-8",
            )

            result = self.run_cli(
                "--config",
                str(config),
                "sync",
                "all",
                "--apply",
                env={
                    "PATH": f"{fake_git}:{os.environ['PATH']}",
                    "AISKILLSYNC_FAKE_GIT_LOG": str(log),
                    "AISKILLSYNC_FAKE_GIT_PULL_EXIT": "7",
                },
            )
            created = (codex / "needs-pull").exists()
            log_text = log.read_text(encoding="utf-8") if log.exists() else ""

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"PULL repo local: git -C {bridge} pull --ff-only", result.stdout)
        self.assertIn("ERROR repo local: git pull --ff-only failed with exit 7", result.stdout)
        self.assertIn(f"-C {bridge} pull --ff-only", log_text)
        self.assertFalse(created)

    def test_sync_pull_before_sync_blocks_existing_non_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake_git = self.write_fake_git(base)
            log = base / "git.log"
            bridge = base / "bridge"
            codex = base / "codex"
            codex.mkdir()
            self.write_skill(bridge / "skills", "non-git")
            config = base / "config.yaml"
            config.write_text(
                f"""bridges:
  - name: local
    path: {bridge}
    skills_path: skills
    enabled: true

ai_skill_paths:
  codex: {codex}

sync:
  mode: symlink
  pull_before_sync: true
  clone_if_missing: false
  default_destinations:
    - codex
""",
                encoding="utf-8",
            )

            result = self.run_cli(
                "--config",
                str(config),
                "sync",
                "all",
                "--apply",
                env={
                    "PATH": f"{fake_git}:{os.environ['PATH']}",
                    "AISKILLSYNC_FAKE_GIT_LOG": str(log),
                },
            )
            created = (codex / "non-git").exists()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ERROR repo local: pull_before_sync requires a git repo", result.stdout)
        self.assertFalse(log.exists())
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
