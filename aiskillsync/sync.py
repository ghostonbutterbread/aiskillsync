"""Conservative sync planning and symlink application."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
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
    destinations: tuple[str, ...]
    selected_discoveries: tuple[BridgeDiscovery, ...]
    actions: tuple[SyncAction, ...] = ()
    notices: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
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
    def skips(self) -> tuple[SyncAction, ...]:
        return tuple(action for action in self.actions if action.action == "skip")


def build_sync_plan(
    config: Config,
    selectors: tuple[str, ...],
    destination_names: tuple[str, ...],
    *,
    dry_run: bool,
) -> SyncPlan:
    """Build a sync plan without mutating local repos or destination paths."""

    discoveries = discover_bridges(config)
    selected, selection_errors = select_bridges(discoveries, selectors)
    destinations, destination_errors = select_destinations(config, destination_names)
    errors = [*selection_errors, *destination_errors]
    notices: list[str] = []
    selected_skills: list[Skill] = []

    for discovery in selected:
        bridge = discovery.bridge
        if not bridge.enabled:
            notices.append(f"SKIP bridge {bridge.name}: disabled")
            continue
        if not discovery.root_exists:
            if bridge.repo and config.sync.clone_if_missing:
                notices.append(
                    f"PLAN bridge {bridge.name}: clone-if-missing is configured "
                    f"but not run in Phase 3 ({bridge.repo} -> {bridge.path})"
                )
                errors.append(
                    f"bridge {bridge.name}: local path missing; planned clone is not "
                    f"executed in Phase 3: {bridge.path}"
                )
            else:
                errors.append(
                    f"bridge {bridge.name}: local path missing and clone is unavailable: {bridge.path}"
                )
            continue
        if not discovery.root_is_dir:
            errors.append(f"bridge {bridge.name}: local path is not a directory: {bridge.path}")
            continue
        if not discovery.exists:
            errors.append(
                f"bridge {bridge.name}: local skills path missing: {discovery.skills_dir}"
            )
            continue
        if discovery.missing_skill_md:
            errors.append(
                f"bridge {bridge.name}: skill dirs missing SKILL.md: "
                f"{len(discovery.missing_skill_md)}"
            )
            continue
        if config.sync.pull_before_sync:
            notices.append(
                f"PLAN bridge {bridge.name}: pull-before-sync is configured "
                "but not run in Phase 3"
            )
        selected_skills.extend(discovery.skills)

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
        destinations=destinations,
        selected_discoveries=selected,
        actions=tuple(actions),
        notices=tuple(notices),
        errors=tuple(errors),
        duplicate_skills=duplicate_skills,
    )


def select_bridges(
    discoveries: tuple[BridgeDiscovery, ...], selectors: tuple[str, ...]
) -> tuple[tuple[BridgeDiscovery, ...], tuple[str, ...]]:
    if not selectors:
        return (), ("sync requires at least one bridge selector",)
    if "all" in selectors:
        if len(selectors) > 1:
            return (), ("selector 'all' cannot be combined with bridge names or indexes",)
        return discoveries, ()

    selected: list[BridgeDiscovery] = []
    errors: list[str] = []
    for selector in selectors:
        if selector.isdigit():
            index = int(selector)
            if index < 1 or index > len(discoveries):
                errors.append(f"bridge index out of range: {selector}")
                continue
            candidate = discoveries[index - 1]
        else:
            matches = [item for item in discoveries if item.bridge.name == selector]
            if not matches:
                errors.append(f"unknown bridge selector: {selector}")
                continue
            if len(matches) > 1:
                errors.append(f"ambiguous bridge name: {selector}")
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
        if name not in config.ai_skill_paths:
            errors.append(f"unknown destination: {name}")
            continue
        if name not in selected:
            selected.append(name)
    if not selected and not errors:
        errors.append("no destinations selected")
    return tuple(selected), tuple(errors)


def apply_sync_plan(plan: SyncPlan) -> tuple[str, ...]:
    """Create only missing destination symlinks from an already validated plan."""

    if plan.has_blockers:
        raise ValueError("cannot apply a sync plan with blockers")

    created: list[str] = []
    seen_action_paths: dict[Path, SyncAction] = {}
    for action in plan.links:
        action_path = _canonical_path(action.status.path)
        previous = seen_action_paths.get(action_path)
        if previous is not None:
            raise ValueError(
                "duplicate destination action path: "
                f"{previous.destination}:{previous.skill.name} and "
                f"{action.destination}:{action.skill.name} -> {action_path}"
            )
        seen_action_paths[action_path] = action
    for dest_root in {action.destination_root for action in plan.links}:
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
    for action in plan.links:
        action.destination_root.mkdir(parents=True, exist_ok=True)
        action.status.path.symlink_to(action.skill.path, target_is_directory=True)
        created.append(f"{action.destination}:{action.skill.name}")
    return tuple(created)


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
