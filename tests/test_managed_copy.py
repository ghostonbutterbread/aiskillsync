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
