from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "migrate_aiskillsync_personal.py"
BOUNTY_HARNESS_REPO = "https://github.com/ghostonbutterbread/bug-bounty-harness.git"


def load_helper():
    spec = importlib.util.spec_from_file_location("migration_helper_under_test", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load helper: {HELPER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MigrationHelperTests(unittest.TestCase):
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
    target = pathlib.Path(args[-1])
    if os.environ.get("AISKILLSYNC_FAKE_GIT_CLONE_FAIL") == "1":
        target.mkdir(parents=True, exist_ok=True)
        (target / "partial-checkout.txt").write_text("left behind", encoding="utf-8")
        print("fake clone failed", file=sys.stderr)
        sys.exit(7)
    skill = os.environ.get("AISKILLSYNC_FAKE_GIT_CLONE_SKILL", "cloned-skill")
    (target / ".git").mkdir(parents=True)
    skill_dir = target / "skills" / skill
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("cloned", encoding="utf-8")
    sys.exit(0)

print("unsupported fake git invocation: " + " ".join(args), file=sys.stderr)
sys.exit(9)
""",
            encoding="utf-8",
        )
        git.chmod(0o755)
        return bin_dir

    def patch_personal_paths(self, module, base: Path) -> None:
        bridge_root = base / "projects" / "bug_bounty_harness"
        module.BRIDGES = {
            "bounty-harness": module.Bridge(
                key="bounty-harness",
                label="Bounty Harness",
                root_path=bridge_root,
                skills_path="skills",
                repo_url=BOUNTY_HARNESS_REPO,
                branch="master",
            )
        }
        module.DESTINATIONS = {
            "codex": module.Destination(
                key="codex",
                label="Codex",
                skills_dir=base / ".codex" / "skills",
            ),
            "claude": module.Destination(
                key="claude",
                label="Claude",
                skills_dir=base / ".claude" / "skills",
            ),
            "ghost": module.Destination(
                key="ghost",
                label="Ghost",
                skills_dir=base / ".openclaw" / "workspace" / "skills",
            ),
        }

    def run_helper(
        self,
        module,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", ["migrate_aiskillsync_personal.py", *args]):
            with mock.patch.dict(os.environ, env or {}, clear=False):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = module.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_list_sources_plans_clone_without_mutating(self) -> None:
        module = load_helper()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.patch_personal_paths(module, base)
            bridge_root = module.BRIDGES["bounty-harness"].root_path

            code, stdout, stderr = self.run_helper(
                module,
                ["--bridge", "bounty-harness", "--dest", "codex", "--list-sources"],
            )
            bridge_exists = bridge_root.exists()

        self.assertEqual(code, 0, stdout + stderr)
        self.assertIn("PLAN clone Bounty Harness", stdout)
        self.assertIn(f"git clone --branch master {BOUNTY_HARNESS_REPO}", stdout)
        self.assertFalse(bridge_exists)

    def test_apply_clones_missing_source_before_discovery_and_links(self) -> None:
        module = load_helper()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bin_dir = self.write_fake_git(base)
            git_log = base / "git.log"
            self.patch_personal_paths(module, base)
            bridge_root = module.BRIDGES["bounty-harness"].root_path
            codex_skill = module.DESTINATIONS["codex"].skills_dir / "cloned-skill"

            code, stdout, stderr = self.run_helper(
                module,
                ["--bridge", "bounty-harness", "--dest", "codex", "--apply"],
                env={
                    "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    "AISKILLSYNC_FAKE_GIT_LOG": str(git_log),
                },
            )
            log = git_log.read_text(encoding="utf-8")
            codex_skill_is_link = codex_skill.is_symlink()
            codex_skill_target = codex_skill.resolve()

        self.assertEqual(code, 0, stdout + stderr)
        self.assertIn("CLONE Bounty Harness", stdout)
        self.assertIn(f"clone --branch master {BOUNTY_HARNESS_REPO} {bridge_root}", log)
        self.assertTrue(codex_skill_is_link)
        self.assertEqual(codex_skill_target, bridge_root / "skills" / "cloned-skill")

    def test_failed_clone_reports_conflict_and_keeps_partial_checkout(self) -> None:
        module = load_helper()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bin_dir = self.write_fake_git(base)
            self.patch_personal_paths(module, base)
            bridge_root = module.BRIDGES["bounty-harness"].root_path
            partial = bridge_root / "partial-checkout.txt"

            code, stdout, stderr = self.run_helper(
                module,
                ["--bridge", "bounty-harness", "--dest", "codex", "--apply"],
                env={
                    "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    "AISKILLSYNC_FAKE_GIT_CLONE_FAIL": "1",
                },
            )
            partial_exists = partial.is_file()

        self.assertEqual(code, 1, stdout + stderr)
        self.assertIn("git clone failed with exit 7", stdout)
        self.assertIn("left any partial checkout untouched", stdout)
        self.assertTrue(partial_exists)


if __name__ == "__main__":
    unittest.main()
