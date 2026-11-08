"""Per-entry managed copy ownership and content verification.

Records live beside the configuration, not in consumer-visible skill trees.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

from .config import atomic_write_text


def record_path(config_path: Path, destination: Path) -> Path:
    key = str(destination.parent.resolve(strict=False) / destination.name)
    return config_path.parent / "managed-copies" / (hashlib.sha256(os.fsencode(key)).hexdigest() + ".json")


def snapshot(root: Path) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"copy tree is not a regular directory: {root}")
    result: dict[str, str] = {}

    def visit(directory: Path) -> None:
        # Path.rglob can silently suppress scandir errors, which would make an
        # incomplete tree appear safe to replace or prune.
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = directory / entry.name
                relative = path.relative_to(root).as_posix()
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    raise ValueError(f"copy trees cannot contain symlinks: {path}")
                if stat.S_ISDIR(mode):
                    result[relative] = "directory"
                    visit(path)
                elif stat.S_ISREG(mode):
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
                    result[relative] = digest.hexdigest()
                else:
                    raise ValueError(f"copy trees cannot contain special files: {path}")

    visit(root)
    return result


def load_record(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("version") != 1
            or not all(isinstance(data.get(key), str) and data[key] for key in ("destination", "source", "source_root"))
            or not isinstance(data.get("snapshot"), dict)
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
    if snapshot(action.skill.path) != action.copy_snapshot:
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
        if snapshot(stage) != action.copy_snapshot or snapshot(action.skill.path) != action.copy_snapshot:
            raise ValueError(f"source changed while copying: {action.skill.path}")
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
                  "profile": action.profile_name, "snapshot": action.copy_snapshot}
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
