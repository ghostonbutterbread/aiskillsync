"""Conservative sync planning and symlink application."""

from __future__ import annotations

import subprocess
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
from urllib.parse import urlparse

from .config import BridgeConfig, Config
from .discovery import (
    BridgeDiscovery,
    DestinationStatus,
    Skill,
    classify_destination,
    discover_bridges,
    duplicate_skill_names,
)


MUTABLE_STATUSES = {"missing"}
CONFLICT_STATUSES = {"directory-copy", "unexpected-symlink", "path-conflict"}


@dataclass(frozen=True)
class SyncAction:
    destination: str
    destination_root: Path
    skill: Skill
    status: DestinationStatus
    action: str


@dataclass(frozen=True)
class SyncPlan:
    dry_run: bool
    adopt: bool
    destinations: tuple[str, ...]
    selected_discoveries: tuple[BridgeDiscovery, ...]
    actions: tuple[SyncAction, ...] = ()
    notices: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    backup_root: Path | None = None
    duplicate_skills: dict[str, tuple[Skill, ...]] = field(default_factory=dict)

    @property
    def has_blockers(self) -> bool:
        return bool(self.errors or self.duplicate_skills or self.conflicts)

    @property
    def conflicts(self) -> tuple[SyncAction, ...]:
        return tuple(action for action in self.actions if action.action == "conflict")

    @property
    def links(self) -> tuple[SyncAction, ...]:
        return tuple(action for action in self.actions if action.action == "link")

    @property
    def adoptions(self) -> tuple[SyncAction, ...]:
        return tuple(action for action in self.actions if action.action == "adopt")

    @property
    def skips(self) -> tuple[SyncAction, ...]:
        return tuple(action for action in self.actions if action.action == "skip")


@dataclass(frozen=True)
class RepositoryMaterialization:
    notices: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    skipped_missing_roots: tuple[str, ...] = ()

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


def materialize_repositories_for_sync(
    config: Config,
    selectors: tuple[str, ...],
    *,
    dry_run: bool,
    skip_clone_bridges: frozenset[str] = frozenset(),
    skip_pull_bridges: frozenset[str] = frozenset(),
) -> RepositoryMaterialization:
    """Clone or update selected enabled repos for sync only."""

    selected, selection_errors = select_bridge_configs(config.bridges, selectors)
    if selection_errors:
        return RepositoryMaterialization()

    notices: list[str] = []
    errors: list[str] = []
    skipped_missing_roots: list[str] = []
    for bridge in selected:
        if not bridge.enabled:
            continue

        root_exists = bridge.path.exists()
        if not root_exists:
            if bridge.repo and config.sync.clone_if_missing:
                if bridge.name in skip_clone_bridges:
                    notices.append(
                        f"SKIP repo {bridge.name}: clone skipped because GitHub auth was not confirmed"
                    )
                    skipped_missing_roots.append(bridge.name)
                    continue
                notices.append(_clone_notice(bridge, dry_run=dry_run))
                if not dry_run:
                    error = _clone_bridge(bridge)
                    if error is not None:
                        errors.append(error)
                continue
            continue

        if not bridge.path.is_dir():
            continue

        if config.sync.pull_before_sync:
            if bridge.name in skip_pull_bridges:
                notices.append(
                    f"SKIP repo {bridge.name}: pull skipped because GitHub auth was not confirmed"
                )
                continue
            notices.append(_pull_notice(bridge, dry_run=dry_run))
            if dry_run:
                continue
            if not _is_git_repo_root(bridge.path):
                errors.append(
                    f"repo {bridge.name}: pull_before_sync requires a git repo: {bridge.path}"
                )
                continue
            error = _pull_bridge(bridge)
            if error is not None:
                errors.append(error)

    return RepositoryMaterialization(
        notices=tuple(notices),
        errors=tuple(errors),
        skipped_missing_roots=tuple(skipped_missing_roots),
    )


def build_sync_plan(
    config: Config,
    selectors: tuple[str, ...],
    destination_names: tuple[str, ...],
    *,
    dry_run: bool,
    adopt: bool = False,
    include_skills: tuple[str, ...] = (),
    exclude_skills: tuple[str, ...] = (),
    preflight_notices: tuple[str, ...] = (),
    preflight_errors: tuple[str, ...] = (),
    skipped_missing_repos: tuple[str, ...] = (),
    backup_root: Path | None = None,
) -> SyncPlan:
    """Build a sync plan without mutating local repos or destination paths."""

    discoveries = discover_bridges(config)
    selected, selection_errors = select_bridges(discoveries, selectors)
    destinations, destination_errors = select_destinations(config, destination_names)
    errors = [*selection_errors, *destination_errors]
    errors.extend(preflight_errors)
    notices: list[str] = [*preflight_notices]
    skipped_missing_repo_names = set(skipped_missing_repos)
    include_skill_names = set(include_skills)
    exclude_skill_names = set(exclude_skills)
    selected_skills: list[Skill] = []

    for discovery in selected:
        bridge = discovery.bridge
        if not bridge.enabled:
            notices.append(f"SKIP repo {bridge.name}: disabled")
            continue
        if not discovery.root_exists:
            if bridge.name in skipped_missing_repo_names:
                continue
            if bridge.repo and config.sync.clone_if_missing:
                if not dry_run:
                    errors.append(
                        f"repo {bridge.name}: local path missing after clone step: {bridge.path}"
                    )
            else:
                errors.append(
                    f"repo {bridge.name}: local path missing and clone is unavailable: {bridge.path}"
                )
            continue
        if not discovery.root_is_dir:
            errors.append(f"repo {bridge.name}: local path is not a directory: {bridge.path}")
            continue
        if not discovery.exists:
            notices.append(
                f"SKIP repo {bridge.name}: local skills path missing: {discovery.skills_dir}"
            )
            continue
        if discovery.missing_skill_md:
            errors.append(
                f"repo {bridge.name}: skill dirs missing SKILL.md: "
                f"{len(discovery.missing_skill_md)}"
            )
            continue
        selected_skills.extend(
            skill
            for skill in discovery.skills
            if (not include_skill_names or skill.name in include_skill_names)
            and skill.name not in exclude_skill_names
        )

    selected_names = {skill.name for skill in selected_skills}
    missing_includes = include_skill_names - selected_names
    if missing_includes:
        errors.append(
            "selected skill names not found in selected repos: "
            + ", ".join(sorted(missing_includes))
        )
    if exclude_skill_names:
        notices.append(
            "SKIP migration denylist: " + ", ".join(sorted(exclude_skill_names))
        )

    duplicate_skills = duplicate_skill_names(tuple(selected_skills))
    errors.extend(duplicate_destination_root_errors(config, destinations))
    for dest_name in destinations:
        dest_root = config.ai_skill_paths[dest_name]
        conflict = destination_root_conflict(dest_root)
        if conflict is not None:
            errors.append(f"destination {dest_name}: {conflict}")

    actions: list[SyncAction] = []
    if destinations:
        for dest_name in destinations:
            dest_root = config.ai_skill_paths[dest_name]
            for skill in selected_skills:
                status = classify_destination(dest_root, skill)
                if status.key == "already-linked":
                    action = "skip"
                elif status.key in MUTABLE_STATUSES:
                    action = "link"
                elif status.key in CONFLICT_STATUSES and adopt:
                    action = "adopt"
                elif status.key in CONFLICT_STATUSES:
                    action = "conflict"
                else:
                    action = "conflict"
                actions.append(
                    SyncAction(
                        destination=dest_name,
                        destination_root=dest_root,
                        skill=skill,
                        status=status,
                        action=action,
                    )
                )

    return SyncPlan(
        dry_run=dry_run,
        adopt=adopt,
        destinations=destinations,
        selected_discoveries=selected,
        actions=tuple(actions),
        notices=tuple(notices),
        errors=tuple(errors),
        backup_root=backup_root,
        duplicate_skills=duplicate_skills,
    )


def select_bridges(
    discoveries: tuple[BridgeDiscovery, ...], selectors: tuple[str, ...]
) -> tuple[tuple[BridgeDiscovery, ...], tuple[str, ...]]:
    if not selectors:
        return (), ("sync requires at least one repo selector",)
    if "all" in selectors:
        if len(selectors) > 1:
            return (), ("selector 'all' cannot be combined with repo names or indexes",)
        return discoveries, ()

    selected: list[BridgeDiscovery] = []
    errors: list[str] = []
    for selector in selectors:
        if selector.isdigit():
            index = int(selector)
            if index < 1 or index > len(discoveries):
                errors.append(f"repo index out of range: {selector}")
                continue
            candidate = discoveries[index - 1]
        else:
            matches = [
                item
                for item in discoveries
                if _bridge_matches_selector(item.bridge, selector)
            ]
            if not matches:
                errors.append(f"unknown repo selector: {selector}")
                continue
            if len(matches) > 1:
                errors.append(f"ambiguous repo selector: {selector}")
                continue
            candidate = matches[0]
        if candidate not in selected:
            selected.append(candidate)
    return tuple(selected), tuple(errors)


def select_bridge_configs(
    bridges: tuple[BridgeConfig, ...], selectors: tuple[str, ...]
) -> tuple[tuple[BridgeConfig, ...], tuple[str, ...]]:
    if not selectors:
        return (), ("sync requires at least one repo selector",)
    if "all" in selectors:
        if len(selectors) > 1:
            return (), ("selector 'all' cannot be combined with repo names or indexes",)
        return bridges, ()

    selected: list[BridgeConfig] = []
    errors: list[str] = []
    for selector in selectors:
        if selector.isdigit():
            index = int(selector)
            if index < 1 or index > len(bridges):
                errors.append(f"repo index out of range: {selector}")
                continue
            candidate = bridges[index - 1]
        else:
            matches = [item for item in bridges if _bridge_matches_selector(item, selector)]
            if not matches:
                errors.append(f"unknown repo selector: {selector}")
                continue
            if len(matches) > 1:
                errors.append(f"ambiguous repo selector: {selector}")
                continue
            candidate = matches[0]
        if candidate not in selected:
            selected.append(candidate)
    return tuple(selected), tuple(errors)


def select_destinations(
    config: Config, destination_names: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    requested = destination_names or config.sync.default_destinations
    selected: list[str] = []
    errors: list[str] = []
    for name in requested:
        destination = _resolve_destination_alias(config, name)
        if destination not in config.ai_skill_paths:
            errors.append(f"unknown destination: {name}")
            continue
        if destination not in selected:
            selected.append(destination)
    if not selected and not errors:
        errors.append("no destinations selected")
    return tuple(selected), tuple(errors)


def _resolve_destination_alias(config: Config, name: str) -> str:
    if name == "openclaw" and "ghost" in config.ai_skill_paths:
        return "ghost"
    if name == "ghost" and "ghost" not in config.ai_skill_paths and "openclaw" in config.ai_skill_paths:
        return "openclaw"
    return name


def _bridge_matches_selector(bridge: BridgeConfig, selector: str) -> bool:
    if bridge.name == selector:
        return True
    if bridge.repo is None:
        return False
    return _normalize_repo_url(bridge.repo) == _normalize_repo_url(selector)


def _normalize_repo_url(value: str) -> str:
    return value.strip().rstrip("/")


def apply_sync_plan(plan: SyncPlan) -> tuple[str, ...]:
    """Create missing symlinks and, when requested, adopt existing entries."""

    if plan.has_blockers:
        raise ValueError("cannot apply a sync plan with blockers")
    if plan.adoptions and plan.backup_root is None:
        raise ValueError("adoption requires a backup root")

    created: list[str] = []
    seen_action_paths: dict[Path, SyncAction] = {}
    for action in (*plan.links, *plan.adoptions):
        action_path = _canonical_path(action.status.path)
        previous = seen_action_paths.get(action_path)
        if previous is not None:
            raise ValueError(
                "duplicate destination action path: "
                f"{previous.destination}:{previous.skill.name} and "
                f"{action.destination}:{action.skill.name} -> {action_path}"
            )
        seen_action_paths[action_path] = action
    for dest_root in {action.destination_root for action in (*plan.links, *plan.adoptions)}:
        conflict = destination_root_conflict(dest_root)
        if conflict is not None:
            raise ValueError(conflict)
    for action in plan.links:
        current = classify_destination(action.destination_root, action.skill)
        if current.key != "missing":
            raise ValueError(
                f"destination changed before apply: {action.destination}:{action.skill.name} "
                f"is now {current.label}"
            )
    for action in plan.adoptions:
        current = classify_destination(action.destination_root, action.skill)
        if current.key not in CONFLICT_STATUSES:
            raise ValueError(
                f"destination changed before adoption: {action.destination}:{action.skill.name} "
                f"is now {current.label}"
            )
    for action in plan.adoptions:
        backup_path = _backup_path(plan.backup_root, action)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(action.status.path), str(backup_path))
    for action in plan.links:
        action.destination_root.mkdir(parents=True, exist_ok=True)
        action.status.path.symlink_to(action.skill.path, target_is_directory=True)
        created.append(f"{action.destination}:{action.skill.name}")
    for action in plan.adoptions:
        action.destination_root.mkdir(parents=True, exist_ok=True)
        action.status.path.symlink_to(action.skill.path, target_is_directory=True)
        created.append(f"{action.destination}:{action.skill.name}")
    return tuple(created)


def make_backup_root() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return Path.home() / ".cache" / "aiskillsync-migration" / timestamp


def _backup_path(backup_root: Path | None, action: SyncAction) -> Path:
    if backup_root is None:
        raise ValueError("backup root is required")
    return backup_root / action.destination / action.skill.name


def duplicate_destination_root_errors(
    config: Config, destinations: tuple[str, ...]
) -> tuple[str, ...]:
    by_root: dict[Path, list[str]] = {}
    for name in destinations:
        root = config.ai_skill_paths[name]
        by_root.setdefault(_canonical_path(root), []).append(name)
    return tuple(
        "selected destinations share root path "
        f"{root}: {', '.join(names)}"
        for root, names in sorted(by_root.items(), key=lambda item: str(item[0]))
        if len(names) > 1
    )


def destination_root_conflict(path: Path) -> str | None:
    for ancestor in _paths_to_check(path):
        if (ancestor.exists() or ancestor.is_symlink()) and not ancestor.is_dir():
            if ancestor == path:
                return f"root path is not a directory: {path}"
            return f"root path has non-directory ancestor: {ancestor}"
    return None


def _paths_to_check(path: Path) -> tuple[Path, ...]:
    return (*path.parents, path)


def _canonical_path(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def _clone_bridge(bridge: BridgeConfig) -> str | None:
    command = ["git", "clone"]
    if bridge.branch:
        command.extend(["--branch", bridge.branch])
    command.extend([bridge.repo or "", str(bridge.path)])
    try:
        bridge.path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        return f"repo {bridge.name}: git clone failed to start: {exc}"
    if result.returncode != 0:
        return (
            f"repo {bridge.name}: git clone failed with exit {result.returncode}: "
            f"{_command_output(result)}"
        )
    return None


def _pull_bridge(bridge: BridgeConfig) -> str | None:
    command = ["git", "-C", str(bridge.path), "pull", "--ff-only"]
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        return f"repo {bridge.name}: git pull --ff-only failed to start: {exc}"
    if result.returncode != 0:
        return (
            f"repo {bridge.name}: git pull --ff-only failed with exit "
            f"{result.returncode}: {_command_output(result)}"
        )
    return None


def _is_git_repo_root(path: Path) -> bool:
    if not (path / ".git").exists():
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _clone_notice(bridge: BridgeConfig, *, dry_run: bool) -> str:
    verb = "PLAN" if dry_run else "CLONE"
    branch = f" --branch {bridge.branch}" if bridge.branch else ""
    return (
        f"{verb} repo {bridge.name}: git clone{branch} "
        f"{_redact_repo_url(bridge.repo or '')} {bridge.path}"
    )


def _pull_notice(bridge: BridgeConfig, *, dry_run: bool) -> str:
    verb = "PLAN" if dry_run else "PULL"
    return f"{verb} repo {bridge.name}: git -C {bridge.path} pull --ff-only"


def _command_output(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or "").strip()
    if not text:
        return "no output"
    return _redact_url_credentials(text.splitlines()[0])


def _redact_repo_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    if parsed.username is None and parsed.password is None:
        return value

    hostname = parsed.hostname or ""
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return parsed._replace(netloc=netloc).geturl()


def _redact_url_credentials(text: str) -> str:
    return re.sub(r"([a-z][a-z0-9+.-]*://)[^/\s@]+@", r"\1", text, flags=re.IGNORECASE)
