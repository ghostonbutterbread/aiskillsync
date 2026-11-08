"""Identify Windows-backed destinations in a WSL mount namespace."""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path

from .config import ConfigError


_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")


def _unescape_mount_field(value: str) -> str:
    return _MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def _is_windows_filesystem(filesystem: list[str]) -> bool | None:
    fs_type, source, options = filesystem[:3]
    if fs_type == "drvfs":
        return True
    if fs_type == "virtiofs":
        # WSL drive shares and WSL container mounts use these tags.
        return True if source in {"drvfsa", "drvfs"} else None
    if fs_type != "9p":
        return False
    # WSL2's 9p transport is also used by non-Windows shares. Only the
    # DrvFS source or aname identifies the Windows-backed variant.
    is_drvfs = source.lower() == "drvfs" or any(
        option.lower() == "aname=drvfs" for option in re.split(r"[,;]", options)
    )
    return True if is_drvfs else None


def is_windows_destination(path: Path) -> bool:
    """Identify WSL Windows mounts; raise ConfigError when detection is ambiguous.

    Callers can select copy mode explicitly via ``sync.copy_destinations``.
    """
    if platform.system() != "Linux":
        return False
    try:
        release = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        release = ""
    if not (
        "microsoft" in release.lower()
        or os.environ.get("WSL_INTEROP")
        or os.environ.get("WSL_DISTRO_NAME")
        or os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop")
    ):
        return False
    try:
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        # Non-strict resolve follows existing parent links, but on newer Python
        # versions it can silently leave a symlink loop unresolved. Probe first
        # so a loop (or inaccessible path) is not mistaken for a valid path.
        try:
            path.stat()
        except FileNotFoundError:
            pass
        destination = path.resolve()
    except (OSError, RuntimeError, UnicodeError) as exc:
        raise ConfigError(
            f"cannot determine whether {path} is Windows-backed ({exc}); "
            "add its destination name to sync.copy_destinations to use copy mode"
        ) from exc

    best_depth = -1
    matches: list[bool | None] = []
    for line in mountinfo.splitlines():
        fields, separator, filesystem = line.partition(" - ")
        if not separator:
            continue
        before, after = fields.split(), filesystem.split()
        if len(before) < 6 or len(after) < 3:
            continue
        mountpoint = Path(_unescape_mount_field(before[4]))
        if not mountpoint.is_absolute() or not destination.is_relative_to(mountpoint):
            continue
        depth = len(mountpoint.parts)
        if depth > best_depth:
            best_depth = depth
            matches = []
        if depth == best_depth:
            matches.append(_is_windows_filesystem(after))
    if not matches or None in matches or (True in matches and False in matches):
        raise ConfigError(
            f"cannot determine whether {path} is Windows-backed; "
            "add its destination name to sync.copy_destinations to use copy mode"
        )
    return all(matches)
