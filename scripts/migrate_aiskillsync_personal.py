#!/usr/bin/env python3
"""One-off personal AI skill symlink migration helper for Ryushe.

The script is dry-run by default. Use --apply to remove safe old copies and
recreate provider skill entries as symlinks to the selected bridge source.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


DEFAULT_DESTINATION_KEYS = ("codex", "claude")
STATUS_KEYS = (
    "already-linked",
    "matching-copy",
    "differing-copy",
    "absent",
    "conflicts",
)


@dataclass(frozen=True)
class Bridge:
    key: str
    label: str
    skills_dir: Path


@dataclass(frozen=True)
class Destination:
    key: str
    label: str
    skills_dir: Path


@dataclass(frozen=True)
class SkillSource:
    name: str
    bridge: Bridge
    path: Path
    skill_md: Path


@dataclass
class Action:
    kind: str
    dest_key: str
    skill_name: str
    message: str


@dataclass(frozen=True)
class DestinationStatus:
    key: str
    message: str


@dataclass
class Report:
    removed: list[Action] = field(default_factory=list)
    linked: list[Action] = field(default_factory=list)
    skipped: list[Action] = field(default_factory=list)
    conflicts: list[Action] = field(default_factory=list)
    source_skill_count: int = 0
    status_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    backup_root: Path | None = None
    backup_planned: bool = False

    def add(self, kind: str, dest_key: str, skill_name: str, message: str) -> None:
        action = Action(kind=kind, dest_key=dest_key, skill_name=skill_name, message=message)
        getattr(self, kind).append(action)

    def count_status(self, dest_key: str, status_key: str) -> None:
        counts = self.status_counts.setdefault(dest_key, {key: 0 for key in STATUS_KEYS})
        counts[status_key] += 1

    def total_status_counts(self) -> dict[str, int]:
        totals = {key: 0 for key in STATUS_KEYS}
        for counts in self.status_counts.values():
            for key in STATUS_KEYS:
                totals[key] += counts.get(key, 0)
        return totals


BRIDGES = {
    "bounty-harness": Bridge(
        key="bounty-harness",
        label="Bounty Harness",
        skills_dir=Path("/home/ryushe/projects/bug_bounty_harness/skills"),
    ),
    "bounty-tools": Bridge(
        key="bounty-tools",
        label="Bounty Tools",
        skills_dir=Path("/home/ryushe/projects/bounty-tools/skills"),
    ),
}

DESTINATIONS = {
    "codex": Destination(
        key="codex",
        label="Codex",
        skills_dir=Path.home() / ".agents" / "skills",
    ),
    "claude": Destination(
        key="claude",
        label="Claude",
        skills_dir=Path.home() / ".claude" / "skills",
    ),
    "ghost": Destination(
        key="ghost",
        label="Ghost",
        skills_dir=Path.home() / ".openclaw" / "workspace" / "skills",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Safely migrate Ryushe's AI skill installs to provider symlinks "
            "from Bounty Harness and optional Bounty Tools source skills."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="make filesystem changes; without this flag the script only reports actions",
    )
    parser.add_argument(
        "--dest",
        action="append",
        choices=sorted(DESTINATIONS),
        help=(
            "provider destination to process; repeat for multiple providers. "
            "Defaults to codex and claude; ghost is processed only when explicitly selected."
        ),
    )
    parser.add_argument(
        "--bridge",
        action="append",
        choices=sorted(BRIDGES),
        help=(
            "source bridge to process; repeat for multiple bridges. "
            "By default, all bridges with an existing skills directory are used."
        ),
    )
    parser.add_argument(
        "--no-link",
        action="store_true",
        help="only remove safe existing destination entries; do not recreate symlinks",
    )
    parser.add_argument(
        "--backup-differs",
        action="store_true",
        help=(
            "when a destination skill has the same name but different SKILL.md, "
            "move it to ~/.cache/aiskillsync-migration instead of treating it as a conflict"
        ),
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help=(
            "list source bridge skills and destination status counts only; "
            "do not plan removals, backups, or symlink creation"
        ),
    )
    return parser.parse_args()


def selected_bridges(keys: list[str] | None, report: Report) -> list[Bridge]:
    if keys:
        bridges = [BRIDGES[key] for key in keys]
    else:
        bridges = [bridge for bridge in BRIDGES.values() if bridge.skills_dir.is_dir()]

    existing = []
    for bridge in bridges:
        if bridge.skills_dir.is_dir():
            existing.append(bridge)
        else:
            report.add(
                "skipped",
                "source",
                bridge.key,
                f"{bridge.label} skills directory does not exist: {bridge.skills_dir}",
            )
    return existing


def selected_destinations(keys: list[str] | None) -> list[Destination]:
    if keys:
        return [DESTINATIONS[key] for key in keys]
    return [DESTINATIONS[key] for key in DEFAULT_DESTINATION_KEYS]


def find_source_skills(bridges: list[Bridge], report: Report) -> dict[str, SkillSource]:
    by_name: dict[str, list[SkillSource]] = {}

    for bridge in bridges:
        for child in sorted(bridge.skills_dir.iterdir(), key=lambda item: item.name):
            skill_md = child / "SKILL.md"
            if not child.is_dir() or not skill_md.is_file():
                continue
            source = SkillSource(
                name=child.name,
                bridge=bridge,
                path=child.resolve(),
                skill_md=skill_md.resolve(),
            )
            by_name.setdefault(source.name, []).append(source)

    sources: dict[str, SkillSource] = {}
    for name, candidates in by_name.items():
        if len(candidates) == 1:
            sources[name] = candidates[0]
            continue

        locations = ", ".join(str(candidate.path) for candidate in candidates)
        report.add(
            "conflicts",
            "source",
            name,
            f"multiple selected bridges provide {name}; refusing to choose one: {locations}",
        )

    return sources


def same_skill_md_bytes(destination: Path, source: SkillSource) -> bool:
    destination_skill_md = destination / "SKILL.md"
    if not destination_skill_md.is_file():
        return False
    return destination_skill_md.read_bytes() == source.skill_md.read_bytes()


def describe_link(path: Path) -> str:
    try:
        return f"{path} -> {path.readlink()}"
    except OSError:
        return str(path)


def make_backup_root() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return Path.home() / ".cache" / "aiskillsync-migration" / timestamp


def backup_destination_path(backup_root: Path, dest: Destination, source: SkillSource) -> Path:
    return backup_root / dest.key / source.name


def classify_destination(destination_path: Path, source: SkillSource) -> DestinationStatus:
    if destination_path.is_symlink():
        current_target = destination_path.readlink()
        resolved_target = (destination_path.parent / current_target).resolve()
        if resolved_target == source.path:
            return DestinationStatus(
                "already-linked",
                f"already linked: {describe_link(destination_path)}",
            )
        return DestinationStatus(
            "differing-copy",
            f"destination symlink points elsewhere: {describe_link(destination_path)}",
        )

    if not destination_path.exists():
        return DestinationStatus("absent", f"destination entry is absent: {destination_path}")

    if destination_path.is_dir():
        if same_skill_md_bytes(destination_path, source):
            return DestinationStatus("matching-copy", f"matching directory: {destination_path}")
        return DestinationStatus(
            "differing-copy",
            f"destination directory differs from source SKILL.md: {destination_path}",
        )

    return DestinationStatus(
        "conflicts",
        f"destination path is not a symlink or directory; left untouched: {destination_path}",
    )


def remove_or_backup_destination(
    destination_path: Path,
    source: SkillSource,
    dest: Destination,
    status: DestinationStatus,
    apply: bool,
    backup_differs: bool,
    backup_root: Path | None,
    report: Report,
) -> tuple[bool, bool]:
    if status.key == "absent":
        return True, False

    if status.key == "already-linked":
        report.add("skipped", dest.key, source.name, status.message)
        return False, False

    if status.key == "matching-copy":
        report.add(
            "removed",
            dest.key,
            source.name,
            f"remove matching directory {destination_path}",
        )
        if apply:
            shutil.rmtree(destination_path)
        return True, True

    if status.key == "differing-copy" and backup_differs:
        if backup_root is None:
            raise RuntimeError("backup root is required when --backup-differs is used")
        backup_path = backup_destination_path(backup_root, dest, source)
        report.backup_root = backup_root
        report.backup_planned = True
        report.add(
            "removed",
            dest.key,
            source.name,
            f"backup differing entry {destination_path} -> {backup_path}",
        )
        if apply:
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination_path), str(backup_path))
        return True, True

    report.add(
        "conflicts",
        dest.key,
        source.name,
        status.message,
    )
    return False, False


def link_destination(
    destination_path: Path,
    source: SkillSource,
    dest: Destination,
    apply: bool,
    planned_removal: bool,
    report: Report,
) -> None:
    simulate_absent = planned_removal and not apply

    if destination_path.is_symlink() and not simulate_absent:
        current_target = destination_path.readlink()
        resolved_target = (destination_path.parent / current_target).resolve()
        if resolved_target == source.path:
            report.add(
                "skipped",
                dest.key,
                source.name,
                f"already linked: {describe_link(destination_path)}",
            )
            return

    if destination_path.exists() and not simulate_absent:
        report.add(
            "conflicts",
            dest.key,
            source.name,
            f"destination still exists after cleanup; not linking: {destination_path}",
        )
        return

    report.add(
        "linked",
        dest.key,
        source.name,
        f"link {destination_path} -> {source.path}",
    )
    if apply:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.symlink_to(source.path, target_is_directory=True)


def process_destination(
    dest: Destination,
    sources: dict[str, SkillSource],
    apply: bool,
    no_link: bool,
    backup_differs: bool,
    backup_root: Path | None,
    report: Report,
) -> None:
    for source in sources.values():
        destination_path = dest.skills_dir / source.name
        status = classify_destination(destination_path, source)
        report.count_status(dest.key, status.key)
        removed_or_absent, planned_removal = remove_or_backup_destination(
            destination_path,
            source,
            dest,
            status,
            apply,
            backup_differs,
            backup_root,
            report,
        )
        if not removed_or_absent:
            continue

        if no_link:
            report.add(
                "skipped",
                dest.key,
                source.name,
                f"--no-link set; not creating symlink at {destination_path}",
            )
            continue

        link_destination(destination_path, source, dest, apply, planned_removal, report)


def scan_destination_statuses(
    dest: Destination,
    sources: dict[str, SkillSource],
    report: Report,
) -> None:
    for source in sources.values():
        destination_path = dest.skills_dir / source.name
        status = classify_destination(destination_path, source)
        report.count_status(dest.key, status.key)


def print_sources(bridges: list[Bridge], sources: dict[str, SkillSource]) -> None:
    print("Selected source bridges:")
    for bridge in bridges:
        bridge_sources = [source.name for source in sources.values() if source.bridge == bridge]
        print(f"  {bridge.key}: {bridge.skills_dir} ({len(bridge_sources)} skills)")
        for name in sorted(bridge_sources):
            print(f"    - {name}")


def print_status_counts(report: Report) -> None:
    if not report.status_counts:
        return

    print("\nDestination status counts:")
    for dest_key, counts in report.status_counts.items():
        print(f"  {dest_key}:")
        for key in STATUS_KEYS:
            print(f"    {key}: {counts.get(key, 0)}")

    totals = report.total_status_counts()
    print("  total:")
    for key in STATUS_KEYS:
        print(f"    {key}: {totals.get(key, 0)}")


def print_report(report: Report, dry_run: bool) -> None:
    mode = "DRY RUN" if dry_run else "APPLIED"
    print(f"\nHealth report ({mode})")
    print("=" * 72)
    print(f"source skills: {report.source_skill_count}")
    print_status_counts(report)
    if report.backup_planned and report.backup_root is not None:
        verb = "planned" if dry_run else "used"
        print(f"backup root {verb}: {report.backup_root}")
    print(f"removed:   {len(report.removed)}")
    print(f"linked:    {len(report.linked)}")
    print(f"skipped:   {len(report.skipped)}")
    print(f"conflicts: {len(report.conflicts)}")

    for title, actions in (
        ("Removed", report.removed),
        ("Linked", report.linked),
        ("Skipped", report.skipped),
        ("Conflicts", report.conflicts),
    ):
        if not actions:
            continue
        print(f"\n{title}:")
        for action in actions:
            print(f"  [{action.dest_key}] {action.skill_name}: {action.message}")


def main() -> int:
    args = parse_args()
    report = Report()
    bridges = selected_bridges(args.bridge, report)
    destinations = selected_destinations(args.dest)

    if not bridges:
        print("No source bridge skills directories were found.", file=sys.stderr)
        print_report(report, dry_run=not args.apply)
        return 1

    sources = find_source_skills(bridges, report)
    report.source_skill_count = len(sources)
    if not sources:
        print("No source skills with SKILL.md were found.", file=sys.stderr)
        print_report(report, dry_run=not args.apply)
        return 1

    if args.list_sources:
        print_sources(bridges, sources)
        print("\nSelected destinations:")
        for dest in destinations:
            print(f"  {dest.key}: {dest.skills_dir}")
            scan_destination_statuses(dest, sources, report)
        print_report(report, dry_run=True)
        return 1 if report.conflicts else 0

    print("Selected source bridges:")
    for bridge in bridges:
        print(f"  {bridge.key}: {bridge.skills_dir}")
    print("\nSelected destinations:")
    for dest in destinations:
        print(f"  {dest.key}: {dest.skills_dir}")
    print(f"\nDiscovered {len(sources)} source skills.")

    backup_root = make_backup_root() if args.backup_differs else None

    for dest in destinations:
        process_destination(
            dest=dest,
            sources=sources,
            apply=args.apply,
            no_link=args.no_link,
            backup_differs=args.backup_differs,
            backup_root=backup_root,
            report=report,
        )

    print_report(report, dry_run=not args.apply)
    return 1 if report.conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
