"""Explicit agent discovery and opt-in native projection."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any

from .config import BridgeConfig, Config, ConfigError, _strip_inline_comment, canonical_path, parse_simple_yaml
from .lanes import effective_config
from .sync import configured_branch_error, destination_root_conflict, select_bridge_configs, make_backup_root


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    path: Path
    bridge: BridgeConfig


@dataclass(frozen=True)
class AgentAction:
    destination: str
    path: Path
    agent: AgentDefinition
    action: str
    expected_state: tuple[object, ...]
    content: str | None = None
    source_digest: str | None = None
    previous_bytes: bytes | None = None
    root_path: Path | None = None
    root_identity: tuple[int, int] | None = None


@dataclass(frozen=True)
class AgentSyncPlan:
    dry_run: bool
    actions: tuple[AgentAction, ...] = ()
    notices: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    backup_root: Path | None = None
    source_roots: tuple[Path, ...] = ()
    source_files: frozenset[Path] = frozenset()

    @property
    def has_blockers(self) -> bool:
        return bool(self.errors or any(action.action == "conflict" for action in self.actions))


def _agent_metadata(lines: list[str]) -> dict[str, object]:
    """Read the two required top-level fields, leaving provider fields untouched.

    This is deliberately not a general YAML parser or provider schema validator.
    Required fields support the config's scalar subset plus block descriptions.
    """
    metadata: dict[str, object] = {}
    for index, line in enumerate(lines):
        match = re.match(r"^(name|description):(?:\s+(.*))?$", line)
        if match is None:
            continue
        key, value = match.group(1), _strip_inline_comment(match.group(2) or "").strip()
        if key in metadata:
            raise ValueError(f"duplicate {key} field")
        if key == "description" and value in {"|", "|-", "|+", ">", ">-", ">+"}:
            block: list[str] = []
            for continuation in lines[index + 1:]:
                if continuation and not continuation[0].isspace():
                    break
                block.append(continuation.strip())
            metadata[key] = (" " if value.startswith(">") else "\n").join(block).strip()
        else:
            metadata[key] = parse_simple_yaml(f"{key}: {value}")[key]
    return metadata


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _native_content(agent: AgentDefinition, provider: str, overlay: dict[str, Any] | None = None) -> tuple[str, str]:
    source_bytes = agent.path.read_bytes()
    # Keep universal-newline rendering while fingerprinting the original bytes.
    source = source_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    lines = source.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError(f"{agent.path}: source frontmatter changed during planning")
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.rstrip("\r\n") == "---")
    except StopIteration as exc:
        raise ValueError(f"{agent.path}: source frontmatter changed during planning") from exc
    metadata = _agent_metadata([line.rstrip("\r\n") for line in lines[1:end]])
    if metadata.get("name") != agent.name or metadata.get("description") != agent.description:
        raise ValueError(f"{agent.path}: source metadata changed during planning")
    # Do not silently drop source permissions, skill preloads, model routing, or
    # unknown provider fields. An explicit opt-in to parent discovery is safe
    # to project as a discoverable native role; exact advertisement is provider-dependent.
    for line in lines[1:end]:
        if line[:1].isspace() or not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([\w-]+):", line)
        if match and match.group(1) == "advertise" and _strip_inline_comment(line.split(":", 1)[1]).strip() == "true":
            continue
        if not match or match.group(1) not in ("name", "description"):
            raise ValueError(f"{agent.path}: cannot translate source frontmatter field {line.strip()!r}")
    prompt = "".join(lines[end + 1:])
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    if provider == "codex":
        body = (f"name = {quote(agent.name)}\ndescription = {quote(agent.description)}\n"
                f"developer_instructions = {quote(prompt)}\n")
        prefix = ""
    elif provider == "opencode":
        body = (f"description: {quote(agent.description)}\nmode: subagent\n"
                f"permission:\n  task: deny\n---\n{prompt}")
        prefix = "---\n"
    elif provider == "omp":
        if agent.name in {"main", "sub"}:
            raise ValueError(f"{agent.path}: OMP reserves the agent name {agent.name!r}")
        overlay = overlay or {}
        fields = overlay.get("frontmatter", {})
        body = f"name: {quote(agent.name)}\ndescription: {quote(agent.description)}\n"
        for key, value in sorted(fields.items()):
            body += f"{key}: {json.dumps(value, ensure_ascii=False)}\n"
        instruction = overlay.get("instructions")
        if instruction:
            prompt += ("\n" if prompt.endswith("\n") else "\n\n") + instruction + "\n"
        body += "---\n" + prompt
        prefix = "---\n"
    else:
        raise ValueError(f"unknown native provider: {provider}")
    identity = _digest(str(agent.path))
    kind = "omp" if provider == "omp" else "native"
    marker = f"# aiskillsync-{kind}-v1 {identity} {_digest(body)}\n"
    return prefix + marker + body, hashlib.sha256(source_bytes).hexdigest()


def _stable_file_bytes(path: Path) -> bytes:
    """Read one regular inode; reject changes made while reading it."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise OSError("safe no-follow file reads are unavailable")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"not a regular file: {path}")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read()
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ):
            raise ValueError(f"file changed while reading: {path}")
        return data
    finally:
        os.close(fd)


def _owned_native(path: Path, agent: AgentDefinition, provider: str) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        text = _stable_file_bytes(path).decode("utf-8")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        marked = normalized[4:] if normalized.startswith("---\n") else normalized
        marker, body = marked.split("\n", 1)
        kind = "omp" if provider == "omp" else "native"
        if marker == f"# aiskillsync-{kind}-v1 {_digest(str(agent.path))} {_digest(body)}":
            return text
        return None
    except (OSError, UnicodeError, ValueError):
        return None


def discover_agents(bridge: BridgeConfig) -> tuple[tuple[AgentDefinition, ...], tuple[str, ...]]:
    """Inspect only immediate .md children of an explicitly configured root."""
    root = bridge.agents_dir
    if root is None:
        return (), ()
    agents: list[AgentDefinition] = []
    notices: list[str] = []
    for path in sorted(root.glob("*.md")):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            if not lines or lines[0] != "---":
                notices.append(f"SKIP {path}: no agent frontmatter")
                continue
            end = lines.index("---", 1)
            metadata = _agent_metadata(lines[1:end])
            name = metadata.get("name")
            description = metadata.get("description")
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
                raise ValueError("name must use lowercase letters, digits, and hyphens, starting with a letter or digit")
            if not isinstance(description, str) or not description.strip():
                raise ValueError("description must be a non-empty string")
            agents.append(AgentDefinition(name, description, path.absolute(), bridge))
        except (OSError, UnicodeError, ValueError) as exc:
            notices.append(f"SKIP {path}: invalid agent definition ({exc})")
    return tuple(agents), tuple(notices)


def _source_entries(path: Path) -> set[Path]:
    """Return every directory entry traversed by a source's symlink chain."""
    absolute = path.absolute()
    current = Path(absolute.anchor)
    pending = deque(absolute.parts[1:])
    entries: set[Path] = set()
    followed = 0
    while pending:
        component = pending.popleft()
        if component == "..":
            current = current.parent
            continue
        if component == ".":
            continue
        entry = current / component
        entries.add(entry)
        if not entry.is_symlink():
            current = entry
            continue
        followed += 1
        if followed > 64:
            raise ConfigError(f"agent source symlink chain exceeds 64 entries: {path}")
        target = entry.readlink()
        if target.is_absolute():
            current = Path(target.anchor)
            parts = target.parts[1:]
        else:
            parts = target.parts
        pending.extendleft(reversed(parts))
    return entries


def resolve_agent_sync_request(
    config: Config,
    selectors: tuple[str, ...],
    destination_names: tuple[str, ...],
    profile_name: str | None = None,
    *,
    lane_overrides: dict[str, str] | None = None,
) -> tuple[Config, tuple[BridgeConfig, ...], tuple[str, ...]]:
    """Enforce the same bridge/destination ownership as skill profiles and lanes."""
    config = effective_config(config, lane_overrides)
    if config.sync.mode != "symlink":
        raise ConfigError("agent sync supports only sync.mode: symlink")
    if config.profile_sets:
        if not profile_name:
            raise ConfigError("profile_sets are active; agent sync requires --profile <name>")
        if selectors or destination_names:
            raise ConfigError("--profile cannot be combined with destinations or repo selectors")
        profile = next((item for item in config.profile_sets if item.name == profile_name), None)
        if profile is None:
            raise ConfigError(f"unknown profile: {profile_name}")
        selectors = tuple(source.bridge for source in profile.sources)
        destination_names = tuple(name for name in profile.destinations if name in config.ai_agent_paths)
    elif profile_name:
        raise ConfigError(f"unknown profile: {profile_name}")
    if not destination_names:
        raise ConfigError("select at least one configured agent destination with --dest or --profile")
    unknown = set(destination_names) - set(config.ai_agent_paths)
    if unknown:
        raise ConfigError("unknown agent destination: " + ", ".join(sorted(unknown)))
    bridges, errors = select_bridge_configs(config.bridges, selectors or ("all",))
    if errors:
        raise ConfigError("; ".join(errors))
    bridges = tuple(bridge for bridge in bridges if bridge.enabled and bridge.agents_path is not None)
    if not bridges:
        raise ConfigError("no enabled selected sources configure agents_path")
    return config, bridges, tuple(dict.fromkeys(destination_names))


def build_agent_sync_plan(
    config: Config,
    selectors: tuple[str, ...],
    destination_names: tuple[str, ...],
    *,
    dry_run: bool,
    adopt: bool = False,
    native_adopt: bool | None = None,
    backup_root: Path | None = None,
    profile_name: str | None = None,
    include_agents: tuple[str, ...] = (),
    lane_overrides: dict[str, str] | None = None,
) -> AgentSyncPlan:
    """Plan file projections without changing repositories or destinations."""
    config, bridges, destination_names = resolve_agent_sync_request(
        config, selectors, destination_names, profile_name, lane_overrides=lane_overrides
    )
    errors: list[str] = []
    notices: list[str] = []
    agents: list[AgentDefinition] = []
    pending_clone = False
    for bridge in bridges:
        if not bridge.enabled or bridge.agents_dir is None:
            continue
        if not bridge.path.exists() and dry_run and bridge.repo and config.sync.clone_if_missing:
            notices.append(f"PLAN repo {bridge.name}: agent discovery deferred until clone")
            pending_clone = True
            continue
        error = configured_branch_error(bridge)
        if error:
            errors.append(error)
            continue
        if not bridge.agents_dir.is_dir():
            errors.append(f"repo {bridge.name}: agents path missing or not a directory: {bridge.agents_dir}")
            continue
        found, skipped = discover_agents(bridge)
        agents.extend(found)
        notices.extend(skipped)
    if not agents and not pending_clone:
        errors.append("no valid agent definitions found in selected sources")
    missing = set(include_agents) - {agent.name for agent in agents}
    if missing and not pending_clone:
        errors.append("selected agent names not found: " + ", ".join(sorted(missing)))
    discovered = tuple(agents)
    if include_agents:
        agents = [agent for agent in agents if agent.name in include_agents]
    by_name: dict[str, list[AgentDefinition]] = {}
    for agent in agents:
        by_name.setdefault(agent.name, []).append(agent)
    for name, definitions in by_name.items():
        if len(definitions) > 1:
            errors.append(f"duplicate agent name {name!r}: " + ", ".join(str(agent.path) for agent in definitions))
    actions: list[AgentAction] = []
    # Protect the actual configured source trees, not an entire checkout: a
    # separate project-local .claude/agents directory is a valid destination.
    source_roots = tuple(canonical_path(path) for bridge in config.bridges
                         for path in (bridge.agents_dir, bridge.skills_dir) if path is not None)
    # Individual source files may be symlinks outside those trees. Include all
    # configured source roots, not only this profile, to avoid cross-profile
    # adoption replacing another profile's canonical input.
    source_files = {entry for bridge in config.bridges if bridge.agents_dir is not None
                    for path in bridge.agents_dir.glob("*.md")
                    if path.is_file() or path.is_symlink() for entry in _source_entries(path)}
    # Track the lexical path to each discoverable skill too. Its directory or
    # SKILL.md may transitively reference an agent destination to be adopted.
    for bridge in config.bridges:
        skills_dir = bridge.skills_dir
        if not skills_dir.is_dir():
            continue
        candidates = (skills_dir.rglob("SKILL.md") if bridge.skill_layout == "categorized"
                      else (child / "SKILL.md" for child in skills_dir.iterdir() if child.is_dir()))
        for skill_md in candidates:
            if skill_md.is_file() or skill_md.is_symlink():
                source_files.update(_source_entries(skill_md))
    for destination in destination_names:
        if not pending_clone:
            unknown_overlays = set(config.agent_overlays.get(destination, {})) - {agent.name for agent in discovered}
            if unknown_overlays:
                errors.append(f"agent overlays for {destination} not found in selected sources: " + ", ".join(sorted(unknown_overlays)))
        root = config.ai_agent_paths[destination]
        resolved_root = canonical_path(root)
        if any(resolved_root.is_relative_to(source) or source.is_relative_to(resolved_root)
               for source in source_roots):
            errors.append(f"agent destination {destination}: root overlaps configured source directory: {root}")
            continue
        try:
            root_identity = _planned_root_identity(root, resolved_root, source_roots, frozenset(source_files))
        except (OSError, ValueError) as exc:
            errors.append(f"agent destination {destination}: root path is not a directory or cannot be bound: {root}: {exc}")
            continue
        conflict = destination_root_conflict(root)
        if conflict:
            errors.append(f"destination {destination}: {conflict}")
            continue

        # The marker contains only a source-path digest, not a manifest with a
        # reversible path. Unmatched managed files must be reconciled by the
        # operator, never silently left active or automatically deleted.
        if root.is_dir() and not pending_clone:
            provider = config.agent_formats.get(destination)
            valid_ids = {_digest(str(agent.path)): agent for agent in discovered}
            for entry in sorted(root.iterdir()):
                if entry.suffix not in {".md", ".toml"}:
                    continue
                if entry.is_file() and not entry.is_symlink():
                    try:
                        with entry.open("rb") as handle:
                            first = handle.readline()
                            marker = handle.readline() if first.rstrip(b"\r\n") == b"---" else first
                        match = re.fullmatch(rb"# aiskillsync-(native|omp)-v1 ([0-9a-f]{64}) [0-9a-f]{64}(?:\r?\n)?", marker)
                        marker_format = ("omp" if match and match.group(1) == b"omp" else
                                         "opencode" if first.rstrip(b"\r\n") == b"---" else "codex")
                        if match:
                            owner = valid_ids.get(match.group(2).decode())
                            expected_name = f"{owner.name}.{'toml' if provider == 'codex' else 'md'}" if owner else None
                            if owner is None or provider is None or marker_format != provider or expected_name != entry.name:
                                errors.append(f"stale or invalid managed agent {entry}: source missing, malformed, renamed, or outside selected sources; preserve and reconcile manually")
                            elif owner not in agents:
                                try:
                                    overlay = config.agent_overlays.get(destination, {}).get(owner.name)
                                    expected_content, _ = _native_content(owner, provider, overlay)
                                    owned_entry = _owned_native(entry, owner, provider)
                                    if owned_entry is None or owned_entry.replace("\r\n", "\n").replace("\r", "\n") != expected_content:
                                        errors.append(f"stale or edited unselected managed agent {entry}: reconcile manually or select it")
                                except (OSError, UnicodeError, ValueError) as exc:
                                    errors.append(f"invalid managed agent source for {entry}: {exc}")
                    except OSError as exc:
                        errors.append(f"cannot inspect agent destination {entry}: {exc}")
                elif entry.is_symlink():
                    target = canonical_path(entry)
                    expected = {canonical_path(agent.path): f"{agent.name}.md" for agent in discovered}
                    selected = {canonical_path(agent.path) for agent in agents}
                    if any(bridge.agents_dir is not None and canonical_path(bridge.agents_dir) in target.parents
                           for bridge in bridges) and (expected.get(target) != entry.name or provider == "codex"
                                                       or (provider is not None and target not in selected)):
                        errors.append(f"stale or invalid managed agent link {entry}: source missing or malformed; preserve and reconcile manually")
        for agent in agents:
            provider = config.agent_formats.get(destination)
            content = None
            source_digest = None
            if provider:
                try:
                    overlay = config.agent_overlays.get(destination, {}).get(agent.name)
                    content, source_digest = _native_content(agent, provider, overlay)
                except (OSError, UnicodeError, ValueError) as exc:
                    errors.append(f"destination {destination}: {exc}")
                    continue
            path = root / f"{agent.name}.{'toml' if provider == 'codex' else 'md'}"
            candidate = canonical_path(path.parent) / path.name
            if candidate in source_files:
                errors.append(f"agent destination {destination}: {path} would replace a source file")
                continue
            expected_state = _entry_state(path)
            owned = None
            if provider:
                if not path.exists() and not path.is_symlink():
                    action = "write"
                else:
                    owned = _owned_native(path, agent, provider)
                    if owned is not None:
                        if owned.replace("\r\n", "\n").replace("\r", "\n") == content:
                            action = "skip"
                        else:
                            action = "refresh"
                    else:
                        action = "adopt" if (adopt if native_adopt is None else native_adopt) else "conflict"
            elif path.is_symlink() and canonical_path(path) == canonical_path(agent.path):
                action = "skip"
            elif not path.exists() and not path.is_symlink():
                action = "link"
            else:
                action = "adopt" if adopt else "conflict"
            try:
                previous_bytes = _stable_file_bytes(path) if action in {"refresh", "adopt"} and path.is_file() and not path.is_symlink() else None
            except (OSError, ValueError) as exc:
                errors.append(f"cannot snapshot agent destination during planning: {path}: {exc}")
                continue
            if action == "refresh" and (owned is None or previous_bytes != owned.encode("utf-8")):
                errors.append(f"agent destination changed between ownership check and snapshot: {path}")
                continue
            if expected_state != _entry_state(path):
                errors.append(f"agent destination changed during planning: {path}")
                continue
            actions.append(AgentAction(destination, path, agent, action, expected_state, content, source_digest,
                                       previous_bytes, resolved_root, root_identity))
        try:
            if canonical_path(root) != resolved_root or _planned_root_identity(root, resolved_root, source_roots, frozenset(source_files)) != root_identity:
                errors.append(f"agent destination {destination}: root changed during planning: {root}")
        except (OSError, ValueError) as exc:
            errors.append(f"agent destination {destination}: root changed during planning: {root}: {exc}")
    if backup_root is None and any(action.action in {"refresh", "adopt"} for action in actions):
        backup_root = make_backup_root()
    return AgentSyncPlan(dry_run, tuple(actions), tuple(notices), tuple(errors), backup_root,
                         source_roots, frozenset(source_files))


def _directory_identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(str(path))
    return info.st_dev, info.st_ino


def _entry_state(path: Path) -> tuple[object, ...]:
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return ("missing",)
    return (stat.st_dev, stat.st_ino, stat.st_mode, stat.st_size, stat.st_mtime_ns,
            str(path.readlink()) if path.is_symlink() else None)



def _fd_identity(fd: int) -> tuple[int, int]:
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


_SUPPORTS_DIR_FD = (
    {os.open, os.mkdir, os.stat, os.rename, os.link, os.symlink, os.unlink,
     os.readlink, os.rmdir}.issubset(os.supports_dir_fd)
    and {os.stat, os.link}.issubset(os.supports_follow_symlinks)
    and os.listdir in os.supports_fd
)


def _open_dir(path: Path, *, create: bool = False,
              forbidden: set[tuple[int, int]] | None = None) -> int:
    """Walk an absolute, resolved path without following any replacement symlink."""
    if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")) or not _SUPPORTS_DIR_FD:
        raise OSError("safe descriptor-relative agent operations are unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                if forbidden and _ancestors(fd) & forbidden:
                    raise ValueError(f"cannot create directory inside a configured source: {path}")
                os.mkdir(part, 0o777, dir_fd=fd)
                child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _ancestors(fd: int) -> set[tuple[int, int]]:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current = os.dup(fd)
    result = set()
    try:
        while True:
            identity = _fd_identity(current)
            result.add(identity)
            parent = os.open('..', flags, dir_fd=current)
            if _fd_identity(parent) == identity:
                os.close(parent)
                return result
            os.close(current)
            current = parent
    finally:
        os.close(current)


def _validate_bound(fd: int, plan: AgentSyncPlan, target_name: str | None = None,
                    protected_fds: tuple[int, ...] = ()) -> None:
    """Check the opened directory, not merely its potentially replaced pathname."""
    identity = _fd_identity(fd)
    ancestors = _ancestors(fd)
    for source_fd in protected_fds:
        if _fd_identity(source_fd) in ancestors or identity in _ancestors(source_fd):
            raise ValueError("agent directory overlaps a bound configured source")
    for source in plan.source_roots:
        try:
            source_fd = _open_dir(source)
        except FileNotFoundError:
            continue
        try:
            if _fd_identity(source_fd) in ancestors or identity in _ancestors(source_fd):
                raise ValueError(f"agent directory overlaps configured source: {source}")
        finally:
            os.close(source_fd)
    if target_name is not None:
        for source in plan.source_files:
            if source.name != target_name:
                continue
            try:
                info = source.parent.stat()
            except FileNotFoundError:
                continue
            if (info.st_dev, info.st_ino) == identity:
                raise ValueError(f"agent destination would replace a source entry: {source}")


def _planned_root_identity(root: Path, resolved: Path, source_roots: tuple[Path, ...],
                           source_files: frozenset[Path]) -> tuple[int, int] | None:
    identity = _directory_identity(root)
    if identity is None:
        return None
    fd = _open_dir(resolved)
    try:
        bound = _fd_identity(fd)
        if bound != identity or canonical_path(root) != resolved:
            raise ValueError(f"agent destination root changed during binding: {root}")
        _validate_bound(fd, AgentSyncPlan(False, source_roots=source_roots, source_files=source_files))
        return bound
    finally:
        os.close(fd)


def _entry_at(fd: int, name: str) -> tuple[object, ...]:
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return ("missing",)
    link = os.readlink(name, dir_fd=fd) if stat.S_ISLNK(info.st_mode) else None
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, link)


def _bytes_at(fd: int, name: str) -> bytes:
    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"not a regular file: {name}")
        with os.fdopen(file_fd, 'rb', closefd=False) as stream:
            data = stream.read()
        after = os.fstat(file_fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError(f"file changed while reading: {name}")
        return data
    finally:
        os.close(file_fd)


def _write_exclusive(fd: int, name: str, data: bytes) -> None:
    file_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(file_fd, 'wb') as stream:
        stream.write(data)


def _snapshot(src_fd: int, name: str, dst_fd: int, dst_name: str) -> None:
    info = os.stat(name, dir_fd=src_fd, follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode):
        os.symlink(os.readlink(name, dir_fd=src_fd), dst_name, dir_fd=dst_fd)
    elif stat.S_ISREG(info.st_mode):
        _write_exclusive(dst_fd, dst_name, _bytes_at(src_fd, name))
    elif stat.S_ISDIR(info.st_mode):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        child_src = os.open(name, flags, dir_fd=src_fd)
        try:
            os.mkdir(dst_name, 0o700, dir_fd=dst_fd)
            child_dst = os.open(dst_name, flags, dir_fd=dst_fd)
            try:
                for child in os.listdir(child_src):
                    _snapshot(child_src, child, child_dst, child)
            finally:
                os.close(child_dst)
        finally:
            os.close(child_src)
    else:
        raise ValueError(f"unsupported agent destination type: {name}")


def _atomic_rename_primitive():
    """Return an exclusive fd-relative rename, or fail closed on unsupported OSes."""
    if sys.platform.startswith('linux'):
        name, flag = 'renameat2', 1  # RENAME_NOREPLACE
    elif sys.platform == 'darwin':
        name, flag = 'renameatx_np', 4  # RENAME_EXCL
    else:
        raise OSError(errno.ENOTSUP, 'exclusive agent displacement is unavailable')
    try:
        function = getattr(ctypes.CDLL(None, use_errno=True), name)
    except AttributeError as exc:
        raise OSError(errno.ENOTSUP, 'exclusive agent displacement is unavailable') from exc
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    return function, flag


def _rename_noreplace(src: str, dst: str, *, src_dir_fd: int, dst_dir_fd: int) -> None:
    function, flag = _atomic_rename_primitive()
    if function(src_dir_fd, os.fsencode(src), dst_dir_fd, os.fsencode(dst), flag) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), dst)


def _private_dir(parent_fd: int, prefix: str) -> tuple[str, int]:
    for _ in range(32):
        name = prefix + secrets.token_hex(12)
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        return name, os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    raise OSError("cannot allocate private agent directory")


def _bound_location(fd: int) -> str:
    """Describe a bound inode without claiming its location is stable."""
    device, inode = _fd_identity(fd)
    try:
        location = os.readlink(f'/proc/self/fd/{fd}') if sys.platform.startswith('linux') else None
    except OSError:
        location = None
    observed = f'observed at {location!r} (may have moved again)' if location else 'current location unknown'
    return f'bound directory device={device} inode={inode}, {observed}'


def _assert_root(action: AgentAction, root_fd: int) -> None:
    try:
        current = _directory_identity(action.path.parent)
        unchanged = current == _fd_identity(root_fd) and canonical_path(action.path.parent) == action.root_path
    except OSError as exc:
        raise ValueError(f"agent destination root changed: {action.path.parent}; {_bound_location(root_fd)}") from exc
    if not unchanged:
        raise ValueError(f"agent destination root changed: {action.path.parent}; {_bound_location(root_fd)}")


def _receipt(status: str, action: AgentAction, backup: Path, displaced: Path) -> bytes:
    return (json.dumps({'status': status, 'target': str(action.path), 'snapshot': str(backup),
                        'displaced': str(displaced)}, indent=2) + '\n').encode()


def apply_agent_sync_plan(plan: AgentSyncPlan) -> tuple[str, ...]:
    """Bind each destination and mutate only through its validated directory fd."""
    if plan.has_blockers:
        raise ValueError('cannot apply an agent sync plan with blockers')
    if plan.dry_run:
        return ()
    roots: dict[Path, int] = {}
    protected_fds: list[int] = []
    protected_ids: set[tuple[int, int]] = set()
    backup_parents: dict[Path, Path] = {}
    receipts: list[tuple[int, AgentAction, Path, Path]] = []
    finalized: list[tuple[int, AgentAction, Path, Path]] = []
    created: list[str] = []
    completed: list[Path] = []
    adoptions = tuple(a for a in plan.actions if a.action in {'adopt', 'refresh'})
    try:
        if adoptions:
            _atomic_rename_primitive()
        for source in plan.source_roots:
            try:
                source_fd = _open_dir(source)
            except FileNotFoundError:
                continue
            protected_fds.append(source_fd)
            protected_ids.add(_fd_identity(source_fd))
        seen: set[tuple[Path, str]] = set()
        # Preflight all entries before creating anything.
        for action in plan.actions:
            assert action.root_path is not None
            key = (action.root_path, action.path.name)
            if key in seen:
                raise ValueError(f'duplicate agent destination action path: {action.path}')
            seen.add(key)
            if canonical_path(action.path.parent) != action.root_path:
                raise ValueError(f'agent destination root changed before apply: {action.path.parent}')
            current = _directory_identity(action.path.parent)
            if current != action.root_identity:
                raise ValueError(f'agent destination root changed before apply: {action.path.parent}')
            if current is not None:
                if action.root_path not in roots:
                    roots[action.root_path] = _open_dir(action.root_path)
                root_fd = roots[action.root_path]
                if _fd_identity(root_fd) != current:
                    raise ValueError(f'agent destination root changed before apply: {action.path.parent}')
                _validate_bound(root_fd, plan, action.path.name, tuple(protected_fds))
                _assert_root(action, root_fd)
                if _entry_at(root_fd, action.path.name) != action.expected_state:
                    raise ValueError(f'agent destination changed before apply: {action.path}')
            elif action.expected_state != ('missing',):
                raise ValueError(f'agent destination changed before apply: {action.path}')
            error = configured_branch_error(action.agent.bridge)
            if error:
                raise ValueError(error)
            if not action.agent.path.is_file():
                raise ValueError(f'agent source missing before apply: {action.agent.path}')
            if action.source_digest is not None and hashlib.sha256(action.agent.path.read_bytes()).hexdigest() != action.source_digest:
                raise ValueError(f'agent source changed before apply: {action.agent.path}')
        if adoptions and plan.backup_root is None:
            raise ValueError('agent adoption requires a backup root')
        for action in adoptions:
            assert plan.backup_root is not None
            backup = plan.backup_root / 'agents' / action.destination / action.path.name
            backup_parents[backup] = canonical_path(backup.parent)
            candidate = backup_parents[backup] / backup.name
            if any(candidate.is_relative_to(s) or s.is_relative_to(candidate) for s in plan.source_roots) or candidate in plan.source_files:
                raise ValueError(f'agent backup overlaps configured source: {backup}')
            if any(candidate.is_relative_to(a.root_path) for a in plan.actions if a.root_path is not None):
                raise ValueError(f'agent backup must be outside destination discovery: {backup}')
            index = backup.parent / (backup.name + '.receipt.json')
            if backup.exists() or backup.is_symlink() or index.exists() or index.is_symlink():
                raise ValueError(f'agent backup already exists: {backup}')
        for action in plan.actions:
            if action.action == 'skip':
                continue
            assert action.root_path is not None
            if action.root_path not in roots:
                # An entry created by somebody else since planning is not ours.
                if _directory_identity(action.path.parent) is not None:
                    raise ValueError(f'agent destination root changed before apply: {action.path.parent}')
                roots[action.root_path] = _open_dir(action.root_path, create=True, forbidden=protected_ids)
            root_fd = roots[action.root_path]
            _validate_bound(root_fd, plan, action.path.name, tuple(protected_fds))
            _assert_root(action, root_fd)
            if _entry_at(root_fd, action.path.name) != action.expected_state:
                raise ValueError(f'agent destination changed before apply: {action.path}')
            if action.source_digest is not None and hashlib.sha256(action.agent.path.read_bytes()).hexdigest() != action.source_digest:
                raise ValueError(f'agent source changed before apply: {action.agent.path}')
            private_fd = None
            parent_fd = None
            backup_fd = None
            private_path = None
            backup = None
            try:
                if action.action in {'adopt', 'refresh'}:
                    assert plan.backup_root is not None
                    backup = plan.backup_root / 'agents' / action.destination / action.path.name
                    # Bind the planned parent independently. Following '..' from
                    # the destination fd could instead reach a protected tree
                    # if the directory was moved after the previous check.
                    parent_path = action.root_path.parent
                    while any(parent_path.is_relative_to(a.root_path)
                              for a in plan.actions if a.root_path is not None):
                        if parent_path == parent_path.parent:
                            raise ValueError('cannot place agent backup outside destination discovery')
                        parent_path = parent_path.parent
                    parent_fd = _open_dir(parent_path)
                    if _ancestors(parent_fd) & protected_ids:
                        raise ValueError(f'agent backup parent overlaps a configured source: {parent_path}')
                    if _fd_identity(parent_fd)[0] != _fd_identity(root_fd)[0]:
                        raise ValueError(f'cannot place a same-filesystem agent backup outside destination: {action.path.parent}')
                    private_name, private_fd = _private_dir(parent_fd, '.aiskillsync-agent-backup-')
                    private_path = parent_path / private_name
                    displaced = private_path / 'displaced'
                    _write_exclusive(private_fd, 'receipt.json', _receipt('incomplete', action, backup, displaced))
                    _assert_root(action, root_fd)
                    backup_fd = _open_dir(backup_parents[backup], create=True, forbidden=protected_ids)
                    _validate_bound(backup_fd, plan, backup.name, tuple(protected_fds))
                    _validate_bound(backup_fd, plan, backup.name + '.receipt.json', tuple(protected_fds))
                    _write_exclusive(backup_fd, backup.name + '.receipt.json', json.dumps({'receipt': str(private_path / 'receipt.json')}).encode())
                    _snapshot(root_fd, action.path.name, backup_fd, backup.name)
                    if _entry_at(root_fd, action.path.name) != action.expected_state:
                        raise ValueError(f'agent destination changed during snapshot: {action.path}; receipt: {private_path / "receipt.json"}')
                    if action.previous_bytes is not None and _bytes_at(backup_fd, backup.name) != action.previous_bytes:
                        raise ValueError(f'agent destination bytes changed during snapshot: {action.path}')
                    _assert_root(action, root_fd)
                    _rename_noreplace(action.path.name, 'displaced', src_dir_fd=root_fd, dst_dir_fd=private_fd)
                    if _entry_at(private_fd, 'displaced') != action.expected_state:
                        raise ValueError(f'agent destination changed at displacement: {action.path}; receipt: {private_path / "receipt.json"}')
                    if action.previous_bytes is not None and _bytes_at(private_fd, 'displaced') != action.previous_bytes:
                        raise ValueError(f'agent destination changed during displacement: {action.path}')
                    _assert_root(action, root_fd)
                if action.content is not None:
                    if private_fd is None:
                        parent_fd = os.dup(root_fd)
                        private_name, private_fd = _private_dir(parent_fd, '.aiskillsync-agent-write-')
                    _write_exclusive(private_fd, 'prepared', action.content.encode('utf-8'))
                    os.link('prepared', action.path.name, src_dir_fd=private_fd, dst_dir_fd=root_fd, follow_symlinks=False)
                    completed.append(action.path)
                    _assert_root(action, root_fd)
                    if hashlib.sha256(action.agent.path.read_bytes()).hexdigest() != action.source_digest:
                        raise ValueError(f'agent source changed during apply: {action.agent.path}')
                    os.unlink('prepared', dir_fd=private_fd)
                else:
                    os.symlink(str(action.agent.path), action.path.name, dir_fd=root_fd)
                    completed.append(action.path)
                    _assert_root(action, root_fd)
                if backup is not None:
                    assert private_fd is not None and private_path is not None
                    # Keep receipts incomplete until all destination roots have
                    # survived all actions, including actions at other roots.
                    receipts.append((os.dup(private_fd), action, backup, private_path))
                created.append(f'{action.destination}:{action.agent.name}')
            finally:
                if backup_fd is not None:
                    os.close(backup_fd)
                if private_fd is not None:
                    if backup is None:
                        try:
                            os.unlink('prepared', dir_fd=private_fd)
                        except FileNotFoundError:
                            pass
                    os.close(private_fd)
                    if backup is None and parent_fd is not None:
                        os.rmdir(private_name, dir_fd=parent_fd)
                if parent_fd is not None:
                    os.close(parent_fd)
        def verify_roots() -> None:
            for item in plan.actions:
                assert item.root_path is not None
                if item.root_path in roots:
                    _assert_root(item, roots[item.root_path])

        verify_roots()
        for record in receipts:
            receipt_fd, item, backup, private_path = record
            _write_exclusive(receipt_fd, 'receipt-complete.json',
                             _receipt('complete', item, backup, private_path / 'displaced'))
            verify_roots()
            os.replace('receipt-complete.json', 'receipt.json', src_dir_fd=receipt_fd, dst_dir_fd=receipt_fd)
            finalized.append(record)
            verify_roots()
        verify_roots()
        return tuple(created)
    except (OSError, ValueError) as exc:
        receipt_errors = []
        for receipt_fd, item, backup, private_path in finalized:
            try:
                name = 'receipt-incomplete-' + secrets.token_hex(8) + '.json'
                _write_exclusive(receipt_fd, name, _receipt('incomplete', item, backup, private_path / 'displaced'))
                os.replace(name, 'receipt.json', src_dir_fd=receipt_fd, dst_dir_fd=receipt_fd)
            except OSError as receipt_exc:
                receipt_errors.append(f'{private_path / "receipt.json"}: {receipt_exc}')
        if receipt_errors:
            exc = ValueError(f'{exc}; failed to mark receipts incomplete: {"; ".join(receipt_errors)}')
        if completed or (adoptions and plan.backup_root):
            paths = ', '.join(str(path) for path in completed)
            backup_note = f'; adoption backups: {plan.backup_root / "agents"}' if adoptions and plan.backup_root else ''
            raise ValueError(f'agent apply incomplete after writing configured targets [{paths}] (configured paths may no longer identify the written entries){backup_note}; stop consumers, locate and preserve all current entries and concurrent edits, and manually reconcile or restore backups before retrying: {exc}') from exc
        raise
    finally:
        for fd in roots.values():
            os.close(fd)
        for fd in protected_fds:
            os.close(fd)
        for receipt_fd, _, _, _ in receipts:
            os.close(receipt_fd)
