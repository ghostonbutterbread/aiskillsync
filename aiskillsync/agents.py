"""Explicit agent discovery and opt-in native projection."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
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


@dataclass(frozen=True)
class AgentSyncPlan:
    dry_run: bool
    actions: tuple[AgentAction, ...] = ()
    notices: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    backup_root: Path | None = None

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
            actions.append(AgentAction(destination, path, agent, action, expected_state, content, source_digest, previous_bytes))
    if backup_root is None and any(action.action in {"refresh", "adopt"} for action in actions):
        backup_root = make_backup_root()
    return AgentSyncPlan(dry_run, tuple(actions), tuple(notices), tuple(errors), backup_root)


def _entry_state(path: Path) -> tuple[object, ...]:
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return ("missing",)
    return (stat.st_dev, stat.st_ino, stat.st_mode, stat.st_size, stat.st_mtime_ns,
            str(path.readlink()) if path.is_symlink() else None)


def _displace(action: AgentAction, backup: Path) -> Path:
    """Snapshot separately, then quarantine the actual entry on its filesystem.

    Neither a failed identity check nor a failed publication automatically
    restores anything: an independent entry may now occupy the pathname.
    """
    source = action.path
    backup.parent.mkdir(parents=True, exist_ok=True)
    parent = source.parent.parent if source.parent.parent.stat().st_dev == source.parent.stat().st_dev else source.parent
    try:
        private = Path(tempfile.mkdtemp(prefix=".aiskillsync-agent-backup-", dir=parent))
    except PermissionError:
        if parent == source.parent:
            raise
        private = Path(tempfile.mkdtemp(prefix=".aiskillsync-agent-backup-", dir=source.parent))
    displaced = private / "displaced"
    receipt = private / "receipt.json"
    receipt.write_text(json.dumps({"status": "incomplete", "target": str(source),
                                   "snapshot": str(backup), "displaced": str(displaced)}, indent=2) + "\n")
    with (backup.parent / (backup.name + ".receipt.json")).open("x", encoding="utf-8") as index:
        json.dump({"receipt": str(receipt)}, index)
    if source.is_symlink():
        backup.symlink_to(source.readlink())
    elif source.is_dir():
        shutil.copytree(source, backup, symlinks=True)
    elif source.is_file():
        with source.open("rb") as src, backup.open("xb") as dst:
            shutil.copyfileobj(src, dst)
        shutil.copystat(source, backup)
    else:
        raise ValueError(f"unsupported agent destination type: {source}; receipt: {receipt}")
    if _entry_state(source) != action.expected_state:
        raise ValueError(f"agent destination changed during snapshot: {source}; receipt: {receipt}")
    if action.previous_bytes is not None and backup.read_bytes() != action.previous_bytes:
        raise ValueError(f"agent destination bytes changed during snapshot: {source}; receipt: {receipt}")
    # The destination is inside a newly-created private directory on the same
    # filesystem, so rename cannot replace another pathname entry there.
    os.rename(source, displaced)
    if _entry_state(displaced) != action.expected_state:
        raise ValueError(f"agent destination changed at displacement: {source}; receipt: {receipt}")
    if displaced.is_file() and not displaced.is_symlink() and displaced.read_bytes() != backup.read_bytes():
        raise ValueError(f"agent destination changed during displacement: {source}; receipt: {receipt}")
    return private


def apply_agent_sync_plan(plan: AgentSyncPlan) -> tuple[str, ...]:
    """Apply a non-blocked file projection; a dry-run plan cannot write."""
    if plan.has_blockers:
        raise ValueError("cannot apply an agent sync plan with blockers")
    if plan.dry_run:
        return ()
    seen: set[Path] = set()
    for action in plan.actions:
        path = canonical_path(action.path.parent) / action.path.name
        if path in seen:
            raise ValueError(f"duplicate agent destination action path: {path}")
        seen.add(path)
        conflict = destination_root_conflict(action.path.parent)
        if conflict:
            raise ValueError(conflict)
        if _entry_state(action.path) != action.expected_state:
            raise ValueError(f"agent destination changed before apply: {action.path}")
        error = configured_branch_error(action.agent.bridge)
        if error:
            raise ValueError(error)
        if not action.agent.path.is_file():
            raise ValueError(f"agent source missing before apply: {action.agent.path}")
        if action.source_digest is not None and hashlib.sha256(action.agent.path.read_bytes()).hexdigest() != action.source_digest:
            raise ValueError(f"agent source changed before apply: {action.agent.path}")
    adoptions = tuple(action for action in plan.actions if action.action in {"adopt", "refresh"})
    if adoptions and plan.backup_root is None:
        raise ValueError("agent adoption requires a backup root")
    for action in adoptions:
        assert plan.backup_root is not None
        backup = plan.backup_root / "agents" / action.destination / action.path.name
        index = backup.parent / (backup.name + ".receipt.json")
        if backup.exists() or backup.is_symlink() or index.exists() or index.is_symlink():
            raise ValueError(f"agent backup already exists: {backup}")
    created: list[str] = []
    completed: list[Path] = []
    try:
        for action in plan.actions:
            if action.action == "skip":
                continue
            if _entry_state(action.path) != action.expected_state:
                raise ValueError(f"agent destination changed before apply: {action.path}")
            if action.source_digest is not None and hashlib.sha256(action.agent.path.read_bytes()).hexdigest() != action.source_digest:
                raise ValueError(f"agent source changed before apply: {action.agent.path}")
            action.path.parent.mkdir(parents=True, exist_ok=True)
            private = None
            backup = None
            if action.action in {"adopt", "refresh"}:
                assert plan.backup_root is not None
                backup = plan.backup_root / "agents" / action.destination / action.path.name
                private = _displace(action, backup)
            if action.content is not None:
                if private is None and _entry_state(action.path) != action.expected_state:
                    raise ValueError(f"agent destination changed before apply: {action.path}")
                temp_dir = private or Path(tempfile.mkdtemp(prefix=".aiskillsync-agent-write-", dir=action.path.parent))
                fd, temp_name = tempfile.mkstemp(prefix="prepared-", dir=temp_dir)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                        handle.write(action.content)
                    if action.action in {"write", "adopt", "refresh"}:
                        # Hard-link create refuses a concurrently created destination.
                        os.link(temp_name, action.path)
                        completed.append(action.path)
                        if hashlib.sha256(action.agent.path.read_bytes()).hexdigest() != action.source_digest:
                            raise ValueError(f"agent source changed during apply: {action.agent.path}")
                        os.unlink(temp_name)
                    else:
                        raise ValueError(f"unsupported native action: {action.action}")
                finally:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)
                    if private is None:
                        temp_dir.rmdir()
            else:
                action.path.symlink_to(action.agent.path)
                completed.append(action.path)
            if private is not None:
                assert backup is not None
                receipt = private / "receipt.json"
                complete = private / "receipt-complete.json"
                complete.write_text(json.dumps({"status": "complete", "target": str(action.path),
                                                "snapshot": str(backup), "displaced": str(private / 'displaced')}, indent=2) + "\n")
                os.replace(complete, receipt)
            created.append(f"{action.destination}:{action.agent.name}")
    except (OSError, ValueError) as exc:
        if completed or (adoptions and plan.backup_root):
            paths = ", ".join(str(path) for path in completed)
            backup = f"; adoption backups: {plan.backup_root / 'agents'}" if adoptions and plan.backup_root else ""
            raise ValueError(f"agent apply incomplete after writing [{paths}]{backup}; stop consumers and inspect these paths, preserve concurrent edits, and manually reconcile or restore backups before retrying: {exc}") from exc
        raise
    return tuple(created)
