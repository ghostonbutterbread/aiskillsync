from __future__ import annotations

import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from aiskillsync.config import BridgeConfig, Config, SyncConfig
from aiskillsync.sync import (
    RepositoryMaterialization,
    git_changed_skill_names,
    materialize_repositories_for_sync,
)


class SyncMaterializationTests(unittest.TestCase):
    def test_materialization_runs_independent_bridges_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridges = tuple(
                BridgeConfig(name, None, base / name, "skills", None, True)
                for name in ("first", "second")
            )
            config = Config(
                path=base / "config.yaml",
                bridges=bridges,
                sync=SyncConfig(
                    pull_before_sync=False,
                    clone_if_missing=False,
                    materialization_workers=2,
                ),
            )
            started = threading.Barrier(2, timeout=1)

            def materialize_one(*args: object, **kwargs: object) -> RepositoryMaterialization:
                started.wait()
                bridge = args[1]
                return RepositoryMaterialization(notices=(f"done {bridge.name}",))

            with mock.patch("aiskillsync.sync._materialize_bridge", side_effect=materialize_one):
                result = materialize_repositories_for_sync(config, ("all",), dry_run=False)

        self.assertEqual(result.notices, ("done first", "done second"))
        self.assertFalse(result.errors)


class GitChangeDetectionTests(unittest.TestCase):
    def test_git_diff_reports_added_updated_and_removed_skill_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bridge = BridgeConfig("source", None, root, "skills", None, True)
            self._git(root, "init")
            self._git(root, "config", "user.email", "test@example.invalid")
            self._git(root, "config", "user.name", "Test User")
            self._skill(root, "existing", "before")
            self._skill(root, "removed", "remove me")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "before")
            before = self._git(root, "rev-parse", "HEAD").stdout.strip()

            self._skill(root, "existing", "after")
            self._skill(root, "added", "new")
            for path in (root / "skills" / "removed").iterdir():
                path.unlink()
            (root / "skills" / "removed").rmdir()
            self._git(root, "add", "-A")
            self._git(root, "commit", "-m", "after")
            after = self._git(root, "rev-parse", "HEAD").stdout.strip()

            changes = git_changed_skill_names(bridge, before, after)

        self.assertEqual(changes.added, ("added",))
        self.assertEqual(changes.updated, ("existing",))
        self.assertEqual(changes.removed, ("removed",))

    def _skill(self, root: Path, name: str, text: str) -> None:
        path = root / "skills" / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "SKILL.md").write_text(text, encoding="utf-8")

    def _git(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )