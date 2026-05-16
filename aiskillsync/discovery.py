"""Bridge, skill, and destination discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import BridgeConfig, Config


@dataclass(frozen=True)
class Skill:
    name: str
    bridge: BridgeConfig
    path: Path
    skill_md: Path


@dataclass(frozen=True)
class BridgeDiscovery:
    bridge: BridgeConfig
    exists: bool
    skills_dir: Path
    root_exists: bool
    root_is_dir: bool
    skills: tuple[Skill, ...] = ()
    missing_skill_md: tuple[Path, ...] = ()


@dataclass(frozen=True)
class DestinationStatus:
    key: str
    label: str
    path: Path
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    bridge_discoveries: tuple[BridgeDiscovery, ...]
    duplicate_bridge_names: tuple[str, ...] = ()
    duplicate_skill_names: dict[str, tuple[Skill, ...]] = field(default_factory=dict)
    missing_default_destinations: tuple[str, ...] = ()

    @property
    def has_errors(self) -> bool:
        if self.duplicate_bridge_names or self.duplicate_skill_names:
            return True
        if self.missing_default_destinations:
            return True
        for discovery in self.bridge_discoveries:
            if discovery.bridge.enabled:
                if not discovery.bridge.skills_path:
                    return True
                if discovery.root_exists and not discovery.root_is_dir:
                    return True
                if discovery.exists and discovery.missing_skill_md:
                    return True
                if not discovery.root_exists and not discovery.bridge.repo:
                    return True
        return False


def discover_bridges(config: Config) -> tuple[BridgeDiscovery, ...]:
    discoveries: list[BridgeDiscovery] = []
    for bridge in config.bridges:
        skills_dir = bridge.skills_dir
        root_exists = bridge.path.exists()
        root_is_dir = bridge.path.is_dir()
        if not skills_dir.is_dir():
            discoveries.append(
                BridgeDiscovery(
                    bridge=bridge,
                    exists=False,
                    skills_dir=skills_dir,
                    root_exists=root_exists,
                    root_is_dir=root_is_dir,
                )
            )
            continue

        skills: list[Skill] = []
        missing_skill_md: list[Path] = []
        for child in sorted(skills_dir.iterdir(), key=lambda item: item.name):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if skill_md.is_file():
                skills.append(
                    Skill(
                        name=child.name,
                        bridge=bridge,
                        path=child.resolve(),
                        skill_md=skill_md.resolve(),
                    )
                )
            else:
                missing_skill_md.append(child)
        discoveries.append(
            BridgeDiscovery(
                bridge=bridge,
                exists=True,
                skills_dir=skills_dir,
                root_exists=root_exists,
                root_is_dir=root_is_dir,
                skills=tuple(skills),
                missing_skill_md=tuple(missing_skill_md),
            )
        )
    return tuple(discoveries)


def enabled_skills(discoveries: tuple[BridgeDiscovery, ...]) -> tuple[Skill, ...]:
    return tuple(
        skill
        for discovery in discoveries
        if discovery.bridge.enabled
        for skill in discovery.skills
    )


def duplicate_bridge_names(config: Config) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for bridge in config.bridges:
        if bridge.name in seen:
            duplicates.add(bridge.name)
        seen.add(bridge.name)
    return tuple(sorted(duplicates))


def duplicate_skill_names(skills: tuple[Skill, ...]) -> dict[str, tuple[Skill, ...]]:
    by_name: dict[str, list[Skill]] = {}
    for skill in skills:
        by_name.setdefault(skill.name, []).append(skill)
    return {
        name: tuple(candidates)
        for name, candidates in sorted(by_name.items())
        if len(candidates) > 1
    }


def classify_destination(destination_root: Path, skill: Skill) -> DestinationStatus:
    path = destination_root / skill.name
    if path.is_symlink():
        target = path.readlink()
        resolved = (path.parent / target).resolve()
        if resolved == skill.path:
            return DestinationStatus(
                key="already-linked",
                label="already linked to correct source",
                path=path,
                detail=f"{path} -> {target}",
            )
        return DestinationStatus(
            key="unexpected-symlink",
            label="symlink to unexpected target",
            path=path,
            detail=f"{path} -> {target}",
        )

    if not path.exists():
        return DestinationStatus(
            key="missing",
            label="missing",
            path=path,
            detail=str(path),
        )

    if path.is_dir():
        return DestinationStatus(
            key="directory-copy",
            label="regular directory/copy",
            path=path,
            detail=str(path),
        )

    return DestinationStatus(
        key="path-conflict",
        label="non-directory path conflict",
        path=path,
        detail=str(path),
    )


def destination_summary(
    destinations: dict[str, Path], skills: tuple[Skill, ...]
) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for dest_name, dest_path in destinations.items():
        counts: dict[str, int] = {}
        for skill in skills:
            status = classify_destination(dest_path, skill)
            counts[status.key] = counts.get(status.key, 0) + 1
        summary[dest_name] = counts
    return summary


def build_doctor_report(config: Config) -> DoctorReport:
    discoveries = discover_bridges(config)
    skills = enabled_skills(discoveries)
    missing_default_destinations = tuple(
        dest for dest in config.sync.default_destinations if dest not in config.ai_skill_paths
    )
    return DoctorReport(
        bridge_discoveries=discoveries,
        duplicate_bridge_names=duplicate_bridge_names(config),
        duplicate_skill_names=duplicate_skill_names(skills),
        missing_default_destinations=missing_default_destinations,
    )
