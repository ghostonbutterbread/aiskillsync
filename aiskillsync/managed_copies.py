"""Per-entry managed copy ownership and content verification.

Records live beside the configuration, not in consumer-visible skill trees.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from .config import atomic_write_text


def record_path(config_path: Path, destination: Path) -> Path:
    key = str(destination.parent.resolve(strict=False) / destination.name)
    return config_path.parent / "managed-copies" / (hashlib.sha256(os.fsencode(key)).hexdigest() + ".json")


def snapshot(root: Path, *, source_repo: Path | None = None) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"copy tree is not a regular directory: {root}")
    repo = source_repo.resolve(strict=True) if source_repo is not None else None
    result: dict[str, str] = {}

    def visit(directory: Path, relative_root: Path, ancestors: frozenset[Path]) -> None:
        resolved = directory.resolve(strict=True)
        if resolved in ancestors:
            raise ValueError(f"copy source symlink cycle: {directory}")
        ancestors = ancestors | {resolved}
        # Path.rglob can silently suppress scandir errors.
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = directory / entry.name
                relative_path = relative_root / entry.name
                relative = relative_path.as_posix()
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    if repo is None:
                        raise ValueError(f"copy trees cannot contain symlinks: {path}")
                    try:
                        target = path.resolve(strict=True)
                    except (OSError, RuntimeError) as exc:
                        raise ValueError(f"broken or cyclic copy source symlink: {path}") from exc
                    if not target.is_relative_to(repo):
                        raise ValueError(f"copy source symlink escapes repo: {path}")
                    mode = target.stat().st_mode
                if stat.S_ISDIR(mode):
                    result[relative] = "directory"
                    visit(path, relative_path, ancestors)
                elif stat.S_ISREG(mode):
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
                    result[relative] = f"{mode & 0o111:03o}:{digest.hexdigest()}"
                else:
                    raise ValueError(f"copy trees cannot contain special files: {path}")

    if repo is not None and not root.resolve(strict=True).is_relative_to(repo):
        raise ValueError(f"copy source is outside repo: {root}")
    visit(root, Path(), frozenset())
    return result


def load_record(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("version") != 1
            or not all(isinstance(data.get(key), str) and data[key] for key in ("destination", "source", "source_root"))
            or not isinstance(data.get("snapshot"), dict)
            or ("source_snapshot" in data and not isinstance(data["source_snapshot"], dict))
            or not (data.get("profile") is None or isinstance(data.get("profile"), str))):
            raise ValueError("invalid record")
        return data
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid managed copy record {path}: {exc}") from exc


def records(config_path: Path):
    directory = config_path.parent / "managed-copies"
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            record = load_record(path)
            if record is not None:
                if record_path(config_path, Path(record["destination"])) != path:
                    raise ValueError(f"managed copy record path mismatch: {path}")
                yield path, record


def apply_copy(action) -> None:
    """Apply one plan after rechecking its ownership and snapshots.

    If interrupted between renames, the old tree remains in the named sibling
    backup; unrecorded/changed trees are never implicitly adopted on retry.
    """
    path = action.status.path
    record_file = action.copy_record_path
    current = load_record(record_file)
    if current != action.copy_record:
        raise ValueError(f"managed copy ownership changed before apply: {path}")
    if action.action == "copy-prune":
        if current is None or snapshot(path) != current["snapshot"]:
            raise ValueError(f"managed copy changed before prune: {path}")
        shutil.rmtree(path)
        record_file.unlink()
        return
    if snapshot(action.skill.path, source_repo=action.skill.bridge.path) != action.copy_snapshot:
        raise ValueError(f"source changed before copy: {action.skill.path}")
    if action.action == "copy-create":
        if path.exists() or path.is_symlink():
            raise ValueError(f"destination appeared before copy: {path}")
    elif action.action == "copy-migrate":
        if not path.is_symlink() or path.resolve(strict=False) != action.skill.path.resolve(strict=False):
            raise ValueError(f"legacy symlink changed before copy: {path}")
    elif action.action == "copy-refresh":
        if current is None or snapshot(path) != current["snapshot"]:
            raise ValueError(f"managed copy changed before refresh: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{path.name}.aiskillsync-new-", dir=path.parent))
    backup = None
    try:
        shutil.copytree(action.skill.path, stage, dirs_exist_ok=True, symlinks=False)
        staged_snapshot = snapshot(stage)
        if snapshot(action.skill.path, source_repo=action.skill.bridge.path) != action.copy_snapshot:
            raise ValueError(f"source changed while copying: {action.skill.path}")
        if staged_snapshot != action.copy_snapshot:
            def content_only(values):
                return {name: value.split(":", 1)[-1] for name, value in values.items()}
            if content_only(staged_snapshot) != content_only(action.copy_snapshot):
                raise ValueError(f"staged copy differs from source: {action.skill.path}")
            print(f"WARNING executable bits differ on destination {path}; "
                  "the destination filesystem may not preserve Unix metadata", file=sys.stderr)
        if action.action == "copy-create":
            if path.exists() or path.is_symlink():
                raise ValueError(f"destination appeared while copying: {path}")
        else:
            if action.action == "copy-refresh" and snapshot(path) != current["snapshot"]:
                raise ValueError(f"managed copy changed while copying: {path}")
            if action.action == "copy-migrate" and (not path.is_symlink() or path.resolve(strict=False) != action.skill.path.resolve(strict=False)):
                raise ValueError(f"legacy symlink changed while copying: {path}")
            backup = Path(tempfile.mkdtemp(prefix=f".{path.name}.aiskillsync-backup-", dir=path.parent))
            backup.rmdir()
            path.rename(backup)
        try:
            stage.rename(path)
        except BaseException:
            if backup is not None and not path.exists() and not path.is_symlink():
                backup.rename(path)
                backup = None
            raise
        record = {"version": 1, "destination": str(path.parent.resolve(strict=False) / path.name),
                  "source": str(action.skill.path.resolve(strict=False)),
                  "source_root": str(action.skill.bridge.skills_dir.resolve(strict=False)),
                  "profile": action.profile_name, "snapshot": staged_snapshot,
                  "source_snapshot": action.copy_snapshot}
        atomic_write_text(record_file, json.dumps(record, indent=2, sort_keys=True) + "\n")
        if backup is not None:
            if backup.is_symlink():
                backup.unlink()
            else:
                shutil.rmtree(backup)
            backup = None
    except BaseException as exc:
        if backup is not None:
            raise ValueError(f"copy incomplete; inspect {path} and preserved backup {backup}: {exc}") from exc
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
