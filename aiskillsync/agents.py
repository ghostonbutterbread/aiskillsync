"""Explicit agent discovery and opt-in native projection."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .config import BridgeConfig, Config, ConfigError, _strip_inline_comment, canonical_path, parse_simple_yaml
from .lanes import effective_config
from .sync import configured_branch_error, destination_root_conflict, select_bridge_configs


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


def _native_content(agent: AgentDefinition, provider: str) -> tuple[str, str]:
    source = agent.path.read_text(encoding="utf-8")
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
    # unknown provider fields. Only the canonical portable two-field dialect is
    # currently translated.
    for line in lines[1:end]:
        if line[:1].isspace() or not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([\w-]+):", line)
        if not match or match.group(1) not in ("name", "description"):
            raise ValueError(f"{agent.path}: cannot translate source frontmatter field {line.strip()!r}")
    prompt = "".join(lines[end + 1:])
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    if provider == "codex":
        body = (f"name = {quote(agent.name)}\ndescription = {quote(agent.description)}\n"
                f"developer_instructions = {quote(prompt)}\n")
        prefix = ""
    else:
        body = (f"description: {quote(agent.description)}\nmode: subagent\n"
                f"permission:\n  task: deny\n---\n{prompt}")
        prefix = "---\n"
    identity = _digest(str(agent.path))
    marker = f"# aiskillsync-native-v1 {identity} {_digest(body)}\n"
    return prefix + marker + body, _digest(source)


def _owned_native(path: Path, agent: AgentDefinition) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        marked = text[4:] if text.startswith("---\n") else text
        marker, body = marked.split("\n", 1)
        if marker == f"# aiskillsync-native-v1 {_digest(str(agent.path))} {_digest(body)}":
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


def resolve_agent_sync_request(
    config: Config,
    selectors: tuple[str, ...],
    destination_names: tuple[str, ...],
    profile_name: str | None = None,
) -> tuple[Config, tuple[BridgeConfig, ...], tuple[str, ...]]:
    """Enforce the same bridge/destination ownership as skill profiles and lanes."""
    config = effective_config(config)
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
    backup_root: Path | None = None,
    profile_name: str | None = None,
    include_agents: tuple[str, ...] = (),
) -> AgentSyncPlan:
    """Plan file projections without changing repositories or destinations."""
    config, bridges, destination_names = resolve_agent_sync_request(config, selectors, destination_names, profile_name)
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
    if include_agents:
        agents = [agent for agent in agents if agent.name in include_agents]
    by_name: dict[str, list[AgentDefinition]] = {}
    for agent in agents:
        by_name.setdefault(agent.name, []).append(agent)
    for name, definitions in by_name.items():
        if len(definitions) > 1:
            errors.append(f"duplicate agent name {name!r}: " + ", ".join(str(agent.path) for agent in definitions))
    actions: list[AgentAction] = []
    for destination in destination_names:
        root = config.ai_agent_paths[destination]
        conflict = destination_root_conflict(root)
        if conflict:
            errors.append(f"destination {destination}: {conflict}")
            continue
        for agent in agents:
            provider = config.agent_formats.get(destination)
            content = None
            source_digest = None
            if provider:
                try:
                    content, source_digest = _native_content(agent, provider)
                except (OSError, UnicodeError, ValueError) as exc:
                    errors.append(f"destination {destination}: {exc}")
                    continue
            path = root / f"{agent.name}.{'toml' if provider == 'codex' else 'md'}"
            expected_state = _entry_state(path)
            if provider:
                if not path.exists() and not path.is_symlink():
                    action = "write"
                else:
                    owned = _owned_native(path, agent)
                    if owned is not None:
                        action = "skip" if owned == content else "update"
                    else:
                        # A backup does not reconcile local safety or prompt.
                        action = "conflict"
            elif path.is_symlink() and canonical_path(path) == canonical_path(agent.path):
                action = "skip"
            elif not path.exists() and not path.is_symlink():
                action = "link"
            else:
                action = "adopt" if adopt else "conflict"
            if expected_state != _entry_state(path):
                errors.append(f"agent destination changed during planning: {path}")
                continue
            actions.append(AgentAction(destination, path, agent, action, expected_state, content, source_digest))
    return AgentSyncPlan(dry_run, tuple(actions), tuple(notices), tuple(errors), backup_root)


def _entry_state(path: Path) -> tuple[object, ...]:
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return ("missing",)
    return (stat.st_dev, stat.st_ino, stat.st_mode, stat.st_size, stat.st_mtime_ns,
            str(path.readlink()) if path.is_symlink() else None)


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
        if action.source_digest is not None and _digest(action.agent.path.read_text(encoding="utf-8")) != action.source_digest:
            raise ValueError(f"agent source changed before apply: {action.agent.path}")
    adoptions = tuple(action for action in plan.actions if action.action == "adopt")
    if adoptions and plan.backup_root is None:
        raise ValueError("agent adoption requires a backup root")
    for action in adoptions:
        assert plan.backup_root is not None
        backup = plan.backup_root / "agents" / action.destination / action.path.name
        if backup.exists() or backup.is_symlink():
            raise ValueError(f"agent backup already exists: {backup}")
    created: list[str] = []
    for action in plan.actions:
        if action.action == "skip":
            continue
        action.path.parent.mkdir(parents=True, exist_ok=True)
        if action.content is not None:
            if _entry_state(action.path) != action.expected_state:
                raise ValueError(f"agent destination changed before apply: {action.path}")
            fd, temp_name = tempfile.mkstemp(prefix=".aiskillsync-agent-", dir=action.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                    handle.write(action.content)
                if action.action == "write":
                    # Hard-link create refuses a concurrently created destination.
                    os.link(temp_name, action.path)
                    os.unlink(temp_name)
                else:
                    os.replace(temp_name, action.path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            created.append(f"{action.destination}:{action.agent.name}")
            continue
        backup = None
        if action.action == "adopt":
            assert plan.backup_root is not None
            backup = plan.backup_root / "agents" / action.destination / action.path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(action.path), str(backup))
        try:
            action.path.symlink_to(action.agent.path)
        except OSError:
            if backup is not None and not (action.path.exists() or action.path.is_symlink()):
                shutil.move(str(backup), str(action.path))
            raise
        created.append(f"{action.destination}:{action.agent.name}")
    return tuple(created)
