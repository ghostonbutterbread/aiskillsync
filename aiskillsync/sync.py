"""Conservative sync planning and symlink application."""

from __future__ import annotations

import subprocess
import shutil
from concurrent.futures import ThreadPoolExecutor
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
    def prunes(self) -> tuple[SyncAction, ...]:
        return tuple(action for action in self.actions if action.action == "prune")

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


@dataclass(frozen=True)
class PullResult:
    error: str | None = None
    updated: bool = False


@dataclass(frozen=True)
class GitSkillChanges:
    added: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApplyResult:
    created: tuple[str, ...] = ()
    pruned: tuple[str, ...] = ()


def materialize_repositories_for_sync(
    config: Config,
    selectors: tuple[str, ...],
    *,
    dry_run: bool,
    force_repo_refresh: bool = False,
    skip_clone_bridges: frozenset[str] = frozenset(),
    skip_pull_bridges: frozenset[str] = frozenset(),
) -> RepositoryMaterialization:
    """Clone or update selected enabled repos for sync only."""

    selected, selection_errors = select_bridge_configs(config.bridges, selectors)
    if selection_errors:
        return RepositoryMaterialization()

    groups_by_root: dict[Path, list[BridgeConfig]] = {}
    groups: list[list[BridgeConfig]] = []
    for bridge in selected:
        root = _canonical_path(bridge.path)
        if root not in groups_by_root:
            groups_by_root[root] = []
            groups.append(groups_by_root[root])
        groups_by_root[root].append(bridge)

    def materialize_group(group: list[BridgeConfig]) -> tuple[RepositoryMaterialization, ...]:
        return tuple(
            _materialize_bridge(
                config,
                bridge,
                dry_run=dry_run,
                force_repo_refresh=force_repo_refresh,
                skip_clone=bridge.name in skip_clone_bridges,
                skip_pull=bridge.name in skip_pull_bridges,
            )
            for bridge in group
        )

    worker_count = min(config.sync.materialization_workers, len(groups))
    if worker_count <= 1:
        grouped_results = [materialize_group(group) for group in groups]
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(materialize_group, group) for group in groups]
            grouped_results = [future.result() for future in futures]

    notices: list[str] = []
    errors: list[str] = []
    skipped_missing_roots: list[str] = []
    for group_results in grouped_results:
        for result in group_results:
            notices.extend(result.notices)
            errors.extend(result.errors)
            skipped_missing_roots.extend(result.skipped_missing_roots)
    return RepositoryMaterialization(tuple(notices), tuple(errors), tuple(skipped_missing_roots))


def _materialize_bridge(
    config: Config,
    bridge: BridgeConfig,
    *,
    dry_run: bool,
    force_repo_refresh: bool,
    skip_clone: bool,
    skip_pull: bool,
) -> RepositoryMaterialization:
    if not bridge.enabled:
        return RepositoryMaterialization()
    if not bridge.path.exists():
        if not bridge.repo or not config.sync.clone_if_missing:
            return RepositoryMaterialization()
        if skip_clone:
            return RepositoryMaterialization(
                notices=(f"SKIP repo {bridge.name}: clone skipped because GitHub auth was not confirmed",),
                skipped_missing_roots=(bridge.name,),
            )
        error = None if dry_run else _clone_bridge(bridge)
        return RepositoryMaterialization(
            notices=(_clone_notice(bridge, dry_run=dry_run),),
            errors=(error,) if error is not None else (),
        )
    if not bridge.path.is_dir():
        return RepositoryMaterialization()
    if not force_repo_refresh:
        branch_error = configured_branch_error(bridge)
        if branch_error is not None:
            return RepositoryMaterialization(errors=(branch_error,))
    if not config.sync.pull_before_sync:
        return RepositoryMaterialization()
    if skip_pull:
        return RepositoryMaterialization(
            notices=(f"SKIP repo {bridge.name}: pull skipped because GitHub auth was not confirmed",)
        )
    notice = _force_refresh_notice(bridge, dry_run=dry_run) if force_repo_refresh else _pull_notice(bridge, dry_run=dry_run)
    if dry_run:
        return RepositoryMaterialization(notices=(notice,))
    if not _is_git_repo_root(bridge.path):
        return RepositoryMaterialization(
            notices=(notice,),
            errors=(f"repo {bridge.name}: pull_before_sync requires a git repo: {bridge.path}",),
        )
    before = _git_revision(bridge.path)
    result = _force_refresh_bridge(bridge) if force_repo_refresh else _pull_bridge(bridge)
    if result.error is not None:
        return RepositoryMaterialization(notices=(notice,), errors=(result.error,))
    after = _git_revision(bridge.path)
    changed = result.updated if before is None or after is None else before != after
    if not changed:
        return RepositoryMaterialization(
            notices=(notice, f"UNCHANGED repo {bridge.name}: already up to date")
        )
    notices = [notice]
    if before is not None and after is not None:
        notices.extend(_skill_change_notices(bridge, git_changed_skill_names(bridge, before, after)))
    notices.append(f"UPDATED repo {bridge.name}: pulled new source changes; linked skills now use the updated files")
    return RepositoryMaterialization(notices=tuple(notices))


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
        if skipped_missing_repo_names and not selected_skills:
            notices.append(
                "SKIP selected skill lookup because repo sync was skipped: "
                + ", ".join(sorted(missing_includes))
            )
        else:
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
            if not include_skill_names and not exclude_skill_names:
                actions.extend(_stale_managed_symlink_actions(dest_name, dest_root, selected))

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


def apply_sync_plan(plan: SyncPlan) -> ApplyResult:
    """Create missing symlinks, prune stale managed symlinks, and adopt when requested."""

    if plan.has_blockers:
        raise ValueError("cannot apply a sync plan with blockers")
    if plan.adoptions and plan.backup_root is None:
        raise ValueError("adoption requires a backup root")

    created: list[str] = []
    pruned: list[str] = []
    seen_action_paths: dict[Path, SyncAction] = {}
    for action in (*plan.links, *plan.adoptions, *plan.prunes):
        action_path = _destination_action_path(action.status.path)
        previous = seen_action_paths.get(action_path)
        if previous is not None:
            raise ValueError(
                "duplicate destination action path: "
                f"{previous.destination}:{previous.skill.name} and "
                f"{action.destination}:{action.skill.name} -> {action_path}"
            )
        seen_action_paths[action_path] = action
    for dest_root in {
        action.destination_root for action in (*plan.links, *plan.adoptions, *plan.prunes)
    }:
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
    for action in plan.prunes:
        if not _is_stale_managed_symlink(action):
            raise ValueError(
                f"destination changed before prune: {action.destination}:{action.skill.name} "
                f"is no longer a stale managed symlink"
            )
    for action in plan.adoptions:
        backup_path = _backup_path(plan.backup_root, action)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(action.status.path), str(backup_path))
    for action in plan.prunes:
        action.status.path.unlink()
        pruned.append(f"{action.destination}:{action.skill.name}")
    for action in plan.links:
        action.destination_root.mkdir(parents=True, exist_ok=True)
        action.status.path.symlink_to(action.skill.path, target_is_directory=True)
        created.append(f"{action.destination}:{action.skill.name}")
    for action in plan.adoptions:
        action.destination_root.mkdir(parents=True, exist_ok=True)
        action.status.path.symlink_to(action.skill.path, target_is_directory=True)
        created.append(f"{action.destination}:{action.skill.name}")
    return ApplyResult(created=tuple(created), pruned=tuple(pruned))


def apply_prune_actions(plan: SyncPlan) -> tuple[str, ...]:
    """Remove stale managed symlinks even when unrelated sync blockers exist."""

    pruned: list[str] = []
    seen_action_paths: dict[Path, SyncAction] = {}
    for action in plan.prunes:
        action_path = _destination_action_path(action.status.path)
        previous = seen_action_paths.get(action_path)
        if previous is not None:
            raise ValueError(
                "duplicate prune action path: "
                f"{previous.destination}:{previous.skill.name} and "
                f"{action.destination}:{action.skill.name} -> {action_path}"
            )
        seen_action_paths[action_path] = action
    for dest_root in {action.destination_root for action in plan.prunes}:
        conflict = destination_root_conflict(dest_root)
        if conflict is not None:
            raise ValueError(conflict)
    for action in plan.prunes:
        if not _is_stale_managed_symlink(action):
            raise ValueError(
                f"destination changed before prune: {action.destination}:{action.skill.name} "
                f"is no longer a stale managed symlink"
            )
    for action in plan.prunes:
        action.status.path.unlink()
        pruned.append(f"{action.destination}:{action.skill.name}")
    return tuple(pruned)


def _stale_managed_symlink_actions(
    dest_name: str, dest_root: Path, discoveries: tuple[BridgeDiscovery, ...]
) -> tuple[SyncAction, ...]:
    if not dest_root.is_dir():
        return ()

    managed_roots: dict[Path, BridgeDiscovery] = {}
    current_names_by_root: dict[Path, frozenset[str]] = {}
    for discovery in discoveries:
        if (
            not discovery.bridge.enabled
            or not discovery.root_exists
            or not discovery.root_is_dir
            or not discovery.exists
            or discovery.missing_skill_md
        ):
            continue
        root = _canonical_path(discovery.skills_dir)
        managed_roots[root] = discovery
        current_names_by_root[root] = frozenset(skill.name for skill in discovery.skills)

    if not managed_roots:
        return ()

    all_current_names = frozenset(
        name for names in current_names_by_root.values() for name in names
    )
    actions: list[SyncAction] = []
    for entry in sorted(dest_root.iterdir(), key=lambda item: item.name):
        if not entry.is_symlink():
            continue
        if entry.name in all_current_names:
            continue
        target = _canonical_path(entry)
        for root, discovery in managed_roots.items():
            if not _is_relative_to(target, root):
                continue
            if entry.name in current_names_by_root[root]:
                break
            skill = Skill(
                name=entry.name,
                bridge=discovery.bridge,
                path=target,
                skill_md=target / "SKILL.md",
            )
            actions.append(
                SyncAction(
                    destination=dest_name,
                    destination_root=dest_root,
                    skill=skill,
                    status=DestinationStatus(
                        key="stale-managed-symlink",
                        label="stale managed symlink",
                        path=entry,
                        detail=f"{entry} -> {entry.readlink()}",
                    ),
                    action="prune",
                )
            )
            break
    return tuple(actions)


def _is_stale_managed_symlink(action: SyncAction) -> bool:
    path = action.status.path
    if not path.is_symlink():
        return False
    target = _canonical_path(path)
    skills_root = _canonical_path(action.skill.bridge.skills_dir)
    current_source = skills_root / path.name / "SKILL.md"
    return _is_relative_to(target, skills_root) and not current_source.is_file()


def _destination_action_path(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    return Path.cwd() / expanded


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


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


def configured_branch_error(bridge: BridgeConfig) -> str | None:
    """Return an error when a branch-pinned bridge is not on its configured branch."""
    if bridge.branch is None:
        return None
    if not _is_git_repo_root(bridge.path):
        return f"repo {bridge.name}: configured branch {bridge.branch!r} requires a git checkout: {bridge.path}"
    result = subprocess.run(
        ["git", "-C", str(bridge.path), "symbolic-ref", "--quiet", "--short", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return f"repo {bridge.name}: configured branch {bridge.branch!r} but checkout is detached"
    actual = result.stdout.strip()
    if actual != bridge.branch:
        return f"repo {bridge.name}: configured branch {bridge.branch!r} but checked out {actual!r}"
    return None


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


def _pull_bridge(bridge: BridgeConfig) -> PullResult:
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
        return PullResult(error=f"repo {bridge.name}: git pull --ff-only failed to start: {exc}")
    if result.returncode != 0:
        return PullResult(
            error=(
                f"repo {bridge.name}: git pull --ff-only failed with exit "
                f"{result.returncode}: {_command_output(result)}"
            )
        )
    return PullResult(updated=_pull_output_indicates_update(result))


def _force_refresh_bridge(bridge: BridgeConfig) -> PullResult:
    fetch_command = ["git", "-C", str(bridge.path), "fetch", "--prune"]
    reset_target = _reset_target(bridge)
    reset_command = ["git", "-C", str(bridge.path), "reset", "--hard", reset_target]
    clean_command = ["git", "-C", str(bridge.path), "clean", "-fd"]
    outputs: list[str] = []
    for command, label in (
        (fetch_command, "git fetch --prune"),
        (reset_command, f"git reset --hard {reset_target}"),
        (clean_command, "git clean -fd"),
    ):
        try:
            result = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as exc:
            return PullResult(error=f"repo {bridge.name}: {label} failed to start: {exc}")
        outputs.append(f"{result.stdout}\n{result.stderr}")
        if result.returncode != 0:
            return PullResult(
                error=(
                    f"repo {bridge.name}: {label} failed with exit "
                    f"{result.returncode}: {_command_output(result)}"
                )
            )
    return PullResult(updated=_force_refresh_output_indicates_update(tuple(outputs)))


def _reset_target(bridge: BridgeConfig) -> str:
    if bridge.branch:
        return f"origin/{bridge.branch}"
    return "@{upstream}"


def _pull_output_indicates_update(result: subprocess.CompletedProcess[str]) -> bool:
    output = f"{result.stdout}\n{result.stderr}".lower()
    unchanged_markers = (
        "already up to date",
        "already up-to-date",
        "up to date.",
        "up-to-date.",
    )
    if any(marker in output for marker in unchanged_markers):
        return False
    return bool(output.strip())


def _force_refresh_output_indicates_update(outputs: tuple[str, ...]) -> bool:
    output = "\n".join(outputs).lower()
    unchanged_markers = (
        "already up to date",
        "already up-to-date",
        "head is now at",
    )
    if any(marker in output for marker in unchanged_markers):
        return False
    return bool(output.strip())


def _skill_change_notices(bridge: BridgeConfig, changes: GitSkillChanges) -> tuple[str, ...]:
    notices: list[str] = []
    if changes.added:
        notices.append(f"ADDED skills in repo {bridge.name}: {', '.join(changes.added)}")
    if changes.updated:
        notices.append(f"UPDATED skills in repo {bridge.name}: {', '.join(changes.updated)}")
    if changes.removed:
        notices.append(f"REMOVED skills in repo {bridge.name}: {', '.join(changes.removed)}")
    return tuple(notices)


def git_changed_skill_names(bridge: BridgeConfig, before: str, after: str) -> GitSkillChanges:
    """Classify top-level skill changes from Git paths without recursively hashing content."""
    result = subprocess.run(
        ["git", "-C", str(bridge.path), "diff", "--name-status", before, after, "--", bridge.skills_path],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return GitSkillChanges()
    added: set[str] = set()
    updated: set[str] = set()
    removed: set[str] = set()
    prefix = f"{Path(bridge.skills_path).as_posix().rstrip('/')}/"
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        status, paths = fields[0], fields[1:]
        for path in paths:
            if not path.startswith(prefix):
                continue
            name = path[len(prefix):].split("/", 1)[0]
            if not name:
                continue
            if status.startswith("A"):
                added.add(name)
            elif status.startswith("D"):
                removed.add(name)
            else:
                updated.add(name)
    updated.difference_update(added | removed)
    return GitSkillChanges(tuple(sorted(added)), tuple(sorted(updated)), tuple(sorted(removed)))


def _git_revision(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return None
    revision = result.stdout.strip()
    return revision or None


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


def _force_refresh_notice(bridge: BridgeConfig, *, dry_run: bool) -> str:
    verb = "PLAN" if dry_run else "FORCE"
    reset_target = _reset_target(bridge)
    return (
        f"{verb} repo {bridge.name}: git -C {bridge.path} fetch --prune && "
        f"git -C {bridge.path} reset --hard {reset_target} && "
        f"git -C {bridge.path} clean -fd"
    )


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
