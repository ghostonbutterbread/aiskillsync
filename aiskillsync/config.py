"""Config loading helpers for aiskillsync.

The MVP intentionally stays stdlib-only.  This module implements the small YAML
subset used by the documented config shape instead of depending on PyYAML.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path("~/.config/aiskillsync/config.yaml")

DEFAULT_CONFIG_TEXT = """bridges:
  - name: bounty-harness
    repo: https://github.com/ghostonbutterbread/bug-bounty-harness.git
    path: ~/projects/bug_bounty_harness
    skills_path: skills
    branch: master
    enabled: true

ai_skill_paths:
  codex: ~/.agents/skills
  claude: ~/.claude/skills
  ghost: ~/.openclaw/workspace/skills

sync:
  mode: symlink
  pull_before_sync: true
  clone_if_missing: true
  default_destinations:
    - codex
    - claude
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


@dataclass(frozen=True)
class Config:
    path: Path
    bridges: tuple[BridgeConfig, ...] = field(default_factory=tuple)
    ai_skill_paths: dict[str, Path] = field(default_factory=dict)
    sync: SyncConfig = field(default_factory=SyncConfig)


def expand_path(value: str | Path) -> Path:
    """Expand ``~`` and environment variables without requiring the path to exist."""

    return Path(os.path.expandvars(str(value))).expanduser()


def default_config_path() -> Path:
    return expand_path(DEFAULT_CONFIG_PATH)


def ensure_default_config() -> Path:
    config_path = default_config_path()
    if config_path.exists():
        return config_path
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not create default config {config_path}: {exc}") from exc
    return config_path


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
        bridges=tuple(bridges),
        ai_skill_paths=destinations,
        sync=SyncConfig(
            mode=mode,
            pull_before_sync=pull_before_sync,
            clone_if_missing=clone_if_missing,
            default_destinations=tuple(default_destinations_raw),
        ),
    )


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
