"""Config loading helpers for aiskillsync.

The MVP intentionally stays stdlib-only.  This module implements the small YAML
subset used by the documented config shape instead of depending on PyYAML.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path("~/.config/aiskillsync/config.yaml")
DEFAULT_REPO_DIR = Path("~/.config/aiskillsync/repos")

DEFAULT_CONFIG_TEXT = """repo_dir: ~/.config/aiskillsync/repos

bridges:
  - name: bounty-harness
    repo: https://github.com/ghostonbutterbread/bug-bounty-harness.git
    path: ~/projects/bug_bounty_harness
    skills_path: skills
    branch: master
    enabled: true

ai_skill_paths:
  codex: ~/.codex/skills
  claude: ~/.claude/skills
  ghost: ~/.openclaw/workspace/skills

sync:
  mode: symlink
  pull_before_sync: true
  clone_if_missing: true
  default_destinations:
    - codex
    - claude
  migration_denylist: []
"""


class ConfigError(ValueError):
    """Raised when config cannot be read or validated."""


@dataclass(frozen=True)
class BridgeConfig:
    name: str
    repo: str | None
    path: Path
    skills_path: str
    branch: str | None = None
    enabled: bool = True

    @property
    def skills_dir(self) -> Path:
        skills_path = expand_path(self.skills_path)
        if skills_path.is_absolute():
            return skills_path
        return self.path / skills_path


@dataclass(frozen=True)
class SyncConfig:
    mode: str = "symlink"
    pull_before_sync: bool = True
    clone_if_missing: bool = True
    default_destinations: tuple[str, ...] = ("codex", "claude")
    migration_denylist: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    path: Path
    repo_dir: Path = field(default_factory=lambda: expand_path(DEFAULT_REPO_DIR))
    bridges: tuple[BridgeConfig, ...] = field(default_factory=tuple)
    ai_skill_paths: dict[str, Path] = field(default_factory=dict)
    sync: SyncConfig = field(default_factory=SyncConfig)


def expand_path(value: str | Path) -> Path:
    """Expand ``~`` and environment variables without requiring the path to exist."""

    return Path(os.path.expandvars(str(value))).expanduser()


def default_config_path() -> Path:
    return expand_path(DEFAULT_CONFIG_PATH)


def default_repo_dir() -> Path:
    return expand_path(DEFAULT_REPO_DIR)


def ensure_default_config() -> Path:
    config_path = default_config_path()
    if config_path.exists():
        return config_path
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(config_path, DEFAULT_CONFIG_TEXT)
    except OSError as exc:
        raise ConfigError(f"could not create default config {config_path}: {exc}") from exc
    return config_path


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace ``path`` with ``text`` using a temp file in-place."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def load_config(path: str | Path | None = None) -> Config:
    config_path = expand_path(path) if path is not None else ensure_default_config()
    if not config_path.exists():
        raise ConfigError(f"config does not exist: {config_path}")
    if not config_path.is_file():
        raise ConfigError(f"config path is not a file: {config_path}")
    try:
        raw = parse_simple_yaml(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"could not read config {config_path}: {exc}") from exc
    return config_from_mapping(raw, config_path)


def config_from_mapping(raw: Any, path: Path) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    repo_dir_raw = raw.get("repo_dir", str(DEFAULT_REPO_DIR))
    if not isinstance(repo_dir_raw, str) or not repo_dir_raw:
        raise ConfigError("repo_dir must be a non-empty string")

    bridges_raw = raw.get("bridges", [])
    if bridges_raw is None:
        bridges_raw = []
    if not isinstance(bridges_raw, list):
        raise ConfigError("bridges must be a list")

    bridges: list[BridgeConfig] = []
    for index, item in enumerate(bridges_raw, start=1):
        if not isinstance(item, dict):
            raise ConfigError(f"bridges[{index}] must be a mapping")
        name = require_string(item, "name", f"bridges[{index}]")
        local_path = require_string(item, "path", f"bridges[{index}]")
        skills_path = item.get("skills_path", "skills")
        if not isinstance(skills_path, str) or not skills_path:
            raise ConfigError(f"bridges[{index}].skills_path must be a non-empty string")
        repo = optional_string(item, "repo", f"bridges[{index}]")
        branch = optional_string(item, "branch", f"bridges[{index}]")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(f"bridges[{index}].enabled must be true or false")
        bridges.append(
            BridgeConfig(
                name=name,
                repo=repo,
                path=expand_path(local_path),
                skills_path=skills_path,
                branch=branch,
                enabled=enabled,
            )
        )

    destinations_raw = raw.get("ai_skill_paths", {})
    if destinations_raw is None:
        destinations_raw = {}
    if not isinstance(destinations_raw, dict):
        raise ConfigError("ai_skill_paths must be a mapping")
    destinations: dict[str, Path] = {}
    for key, value in destinations_raw.items():
        if not isinstance(key, str) or not key:
            raise ConfigError("destination names must be non-empty strings")
        if not isinstance(value, str) or not value:
            raise ConfigError(f"ai_skill_paths.{key} must be a non-empty string")
        destinations[key] = expand_path(value)

    sync_raw = raw.get("sync", {})
    if sync_raw is None:
        sync_raw = {}
    if not isinstance(sync_raw, dict):
        raise ConfigError("sync must be a mapping")
    default_destinations_raw = sync_raw.get("default_destinations", ("codex", "claude"))
    if not isinstance(default_destinations_raw, (list, tuple)) or not all(
        isinstance(item, str) and item for item in default_destinations_raw
    ):
        raise ConfigError("sync.default_destinations must be a list of destination names")
    migration_denylist_raw = sync_raw.get("migration_denylist", ())
    if not isinstance(migration_denylist_raw, (list, tuple)) or not all(
        isinstance(item, str) and item for item in migration_denylist_raw
    ):
        raise ConfigError("sync.migration_denylist must be a list of skill names")

    mode = sync_raw.get("mode", "symlink")
    if not isinstance(mode, str) or not mode:
        raise ConfigError("sync.mode must be a non-empty string")
    pull_before_sync = sync_raw.get("pull_before_sync", True)
    clone_if_missing = sync_raw.get("clone_if_missing", True)
    if not isinstance(pull_before_sync, bool):
        raise ConfigError("sync.pull_before_sync must be true or false")
    if not isinstance(clone_if_missing, bool):
        raise ConfigError("sync.clone_if_missing must be true or false")

    return Config(
        path=path,
        repo_dir=expand_path(repo_dir_raw),
        bridges=tuple(bridges),
        ai_skill_paths=destinations,
        sync=SyncConfig(
            mode=mode,
            pull_before_sync=pull_before_sync,
            clone_if_missing=clone_if_missing,
            default_destinations=tuple(default_destinations_raw),
            migration_denylist=tuple(migration_denylist_raw),
        ),
    )


def config_to_text(config: Config) -> str:
    """Serialize config using the simple YAML subset supported by this package."""

    lines: list[str] = [f"repo_dir: {_format_scalar(config.repo_dir)}", ""]

    if config.bridges:
        lines.append("bridges:")
        for bridge in config.bridges:
            lines.append(f"  - name: {_format_scalar(bridge.name)}")
            if bridge.repo is not None:
                lines.append(f"    repo: {_format_scalar(bridge.repo)}")
            lines.append(f"    path: {_format_scalar(bridge.path)}")
            lines.append(f"    skills_path: {_format_scalar(bridge.skills_path)}")
            if bridge.branch is not None:
                lines.append(f"    branch: {_format_scalar(bridge.branch)}")
            lines.append(f"    enabled: {str(bridge.enabled).lower()}")
    else:
        lines.append("bridges: []")

    lines.append("")
    lines.append("ai_skill_paths:")
    for key, value in sorted(config.ai_skill_paths.items()):
        lines.append(f"  {key}: {_format_scalar(value)}")

    lines.append("")
    lines.append("sync:")
    lines.append(f"  mode: {_format_scalar(config.sync.mode)}")
    lines.append(f"  pull_before_sync: {str(config.sync.pull_before_sync).lower()}")
    lines.append(f"  clone_if_missing: {str(config.sync.clone_if_missing).lower()}")
    lines.append("  default_destinations:")
    for destination in config.sync.default_destinations:
        lines.append(f"    - {_format_scalar(destination)}")
    if config.sync.migration_denylist:
        lines.append("  migration_denylist:")
        for skill_name in config.sync.migration_denylist:
            lines.append(f"    - {_format_scalar(skill_name)}")
    lines.append("")
    return "\n".join(lines)


def _format_scalar(value: str | Path) -> str:
    text = str(value)
    if not text:
        return '""'
    if "\n" in text or "\r" in text:
        raise ConfigError("config values cannot contain newlines")
    needs_quotes = (
        text.strip() != text
        or text[0] in "-[]{}#&*!|>'\"%@`"
        or text.lower() in {"true", "false", "null", "~"}
        or " #" in text
    )
    if not needs_quotes:
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def require_string(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{context}.{key} must be a non-empty string")
    return value


def optional_string(mapping: dict[str, Any], key: str, context: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{context}.{key} must be a non-empty string when set")
    return value


def parse_simple_yaml(text: str) -> Any:
    lines = [
        (indent, content, line_number)
        for indent, content, line_number in _logical_lines(text)
    ]
    if not lines:
        return {}
    value, index = _parse_block(lines, 0, lines[0][0])
    if index != len(lines):
        _, _, line_number = lines[index]
        raise ConfigError(f"could not parse config near line {line_number}")
    return value


def _logical_lines(text: str) -> list[tuple[int, str, int]]:
    logical: list[tuple[int, str, int]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        without_comment = _strip_inline_comment(raw_line.rstrip())
        if not without_comment.strip():
            continue
        indent = len(without_comment) - len(without_comment.lstrip(" "))
        if "\t" in without_comment[:indent]:
            raise ConfigError(f"tabs are not supported for indentation near line {line_number}")
        logical.append((indent, without_comment.strip(), line_number))
    return logical


def _strip_inline_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            continue
        if char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
    return line


def _parse_block(
    lines: list[tuple[int, str, int]], index: int, indent: int
) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    current_indent, content, line_number = lines[index]
    if current_indent != indent:
        raise ConfigError(f"unexpected indentation near line {line_number}")
    if content.startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_list(
    lines: list[tuple[int, str, int]], index: int, indent: int
) -> tuple[list[Any], int]:
    items: list[Any] = []
    while index < len(lines):
        current_indent, content, line_number = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent or not content.startswith("- "):
            break

        item_text = content[2:].strip()
        index += 1
        if not item_text:
            if index < len(lines) and lines[index][0] > indent:
                item, index = _parse_block(lines, index, lines[index][0])
            else:
                item = None
            items.append(item)
            continue

        key_value = _split_key_value(item_text)
        if key_value is None:
            items.append(_parse_scalar(item_text, line_number))
            continue

        key, value_text = key_value
        item: dict[str, Any] = {key: _parse_value_text(value_text, line_number)}
        if index < len(lines) and lines[index][0] > indent:
            nested, index = _parse_block(lines, index, lines[index][0])
            if not isinstance(nested, dict):
                raise ConfigError(f"list item mapping expected near line {line_number}")
            item.update(nested)
        items.append(item)
    return items, index


def _parse_mapping(
    lines: list[tuple[int, str, int]], index: int, indent: int
) -> tuple[dict[str, Any], int]:
    mapping: dict[str, Any] = {}
    while index < len(lines):
        current_indent, content, line_number = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent:
            raise ConfigError(f"unexpected indentation near line {line_number}")
        if content.startswith("- "):
            break

        key_value = _split_key_value(content)
        if key_value is None:
            raise ConfigError(f"expected key/value mapping near line {line_number}")
        key, value_text = key_value
        index += 1
        if value_text == "":
            if index < len(lines) and lines[index][0] > indent:
                value, index = _parse_block(lines, index, lines[index][0])
            else:
                value = {}
        else:
            value = _parse_value_text(value_text, line_number)
        if key in mapping:
            raise ConfigError(f"duplicate key {key!r} near line {line_number}")
        mapping[key] = value
    return mapping, index


def _split_key_value(text: str) -> tuple[str, str] | None:
    quote: str | None = None
    for index, char in enumerate(text):
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            continue
        if char == ":":
            key = text[:index].strip()
            value = text[index + 1 :].strip()
            if not key:
                return None
            return key, value
    return None


def _parse_value_text(value_text: str, line_number: int) -> Any:
    if value_text == "":
        return {}
    return _parse_scalar(value_text, line_number)


def _parse_scalar(value: str, line_number: int) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "~"}:
        return None
    if value == "[]":
        return []
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in ("'", '"')
    ):
        return value[1:-1]
    if value.startswith("[") or value.startswith("{"):
        raise ConfigError(f"inline collections are not supported near line {line_number}")
    return value
