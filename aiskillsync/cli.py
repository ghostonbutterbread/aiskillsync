"""Command line interface for aiskillsync."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from . import __version__
from .config import (
    Config,
    ConfigError,
    DEFAULT_CONFIG_TEXT,
    config_from_mapping,
    default_config_path,
    expand_path,
    load_config,
    parse_simple_yaml,
)
from .discovery import (
    BridgeDiscovery,
    build_doctor_report,
    classify_destination,
    destination_summary,
    discover_bridges,
    enabled_skills,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aiskillsync",
        description="Bridge AI skill directories into provider skill paths.",
    )
    parser.add_argument("--version", action="version", version=f"aiskillsync {__version__}")
    parser.add_argument(
        "--config",
        type=Path,
        help=f"config file path (default: {default_config_path()})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create a default config")
    init_parser.add_argument("--dry-run", action="store_true", help="print the config without writing")
    init_parser.add_argument("--force", action="store_true", help="overwrite an existing config")
    init_parser.set_defaults(func=cmd_init)

    config_parser = subparsers.add_parser("config", help="show loaded config")
    config_parser.add_argument(
        "--default",
        "--show-default",
        dest="show_default",
        action="store_true",
        help="print the default config template without loading a config file",
    )
    config_parser.set_defaults(func=cmd_config)

    list_parser = subparsers.add_parser("list", help="show bridges and discovered skills")
    list_parser.set_defaults(func=cmd_list)

    doctor_parser = subparsers.add_parser("doctor", help="validate config and filesystem state")
    doctor_parser.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args, sys.stdout, sys.stderr)
    except ConfigError as exc:
        print(f"aiskillsync: {exc}", file=sys.stderr)
        return 2


def cmd_init(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    config_path = expand_path(args.config) if args.config else default_config_path()
    if args.dry_run:
        print(f"Would write config to {config_path}", file=stdout)
        print(DEFAULT_CONFIG_TEXT.rstrip(), file=stdout)
        return 0

    if config_path.exists() and not args.force:
        print(
            f"config already exists: {config_path}; pass --force to overwrite",
            file=stderr,
        )
        return 1

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    print(f"Wrote config: {config_path}", file=stdout)
    return 0


def cmd_config(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    if args.show_default:
        print(DEFAULT_CONFIG_TEXT.rstrip(), file=stdout)
        return 0

    config = _load_config(args)
    print(f"Config: {config.path}", file=stdout)
    print("", file=stdout)
    print("Bridges:", file=stdout)
    if config.bridges:
        for index, bridge in enumerate(config.bridges, start=1):
            state = "enabled" if bridge.enabled else "disabled"
            print(f"  {index}. {bridge.name} ({state})", file=stdout)
            print(f"     repo: {bridge.repo or '-'}", file=stdout)
            print(f"     path: {bridge.path}", file=stdout)
            print(f"     skills_path: {bridge.skills_path}", file=stdout)
            print(f"     skills_dir: {bridge.skills_dir}", file=stdout)
            print(f"     branch: {bridge.branch or '-'}", file=stdout)
    else:
        print("  none", file=stdout)

    print("", file=stdout)
    print("AI skill paths:", file=stdout)
    if config.ai_skill_paths:
        for key, path in sorted(config.ai_skill_paths.items()):
            print(f"  {key}: {path}", file=stdout)
    else:
        print("  none", file=stdout)

    print("", file=stdout)
    print("Sync:", file=stdout)
    print(f"  mode: {config.sync.mode}", file=stdout)
    print(f"  pull_before_sync: {str(config.sync.pull_before_sync).lower()}", file=stdout)
    print(f"  clone_if_missing: {str(config.sync.clone_if_missing).lower()}", file=stdout)
    print(
        "  default_destinations: "
        + (", ".join(config.sync.default_destinations) if config.sync.default_destinations else "-"),
        file=stdout,
    )
    return 0


def cmd_list(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    config = _load_config(args)
    discoveries = discover_bridges(config)
    skills = enabled_skills(discoveries)
    summary = destination_summary(config.ai_skill_paths, skills)

    print("Bridges:", file=stdout)
    if not discoveries:
        print("  none", file=stdout)
    for index, discovery in enumerate(discoveries, start=1):
        bridge = discovery.bridge
        state = "enabled" if bridge.enabled else "disabled"
        exists = "found" if discovery.exists else "missing"
        print(
            f"  {index}. {bridge.name} [{state}, {exists}] "
            f"{len(discovery.skills)} skills",
            file=stdout,
        )
        print(f"     path: {bridge.path}", file=stdout)
        print(f"     repo: {bridge.repo or '-'}", file=stdout)
        print(f"     branch: {bridge.branch or '-'}", file=stdout)
        print(f"     skills_dir: {discovery.skills_dir}", file=stdout)
        if discovery.skills:
            for skill in discovery.skills:
                print(f"     - {skill.name}", file=stdout)
        if discovery.missing_skill_md:
            print(
                f"     missing SKILL.md dirs: {len(discovery.missing_skill_md)}",
                file=stdout,
            )

    print("", file=stdout)
    print("Destination status summary:", file=stdout)
    if not config.ai_skill_paths:
        print("  none", file=stdout)
    for dest_name, counts in sorted(summary.items()):
        rendered = ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
        print(f"  {dest_name}: {rendered or 'no enabled skills'}", file=stdout)
    return 0


def cmd_doctor(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    config_path = expand_path(args.config) if args.config else default_config_path()
    if not config_path.exists():
        print(f"FAIL config exists: {config_path}", file=stdout)
        return 1
    print(f"OK config exists: {config_path}", file=stdout)

    try:
        text = config_path.read_text(encoding="utf-8")
        raw = parse_simple_yaml(text)
        config = config_from_mapping(raw, config_path)
    except (OSError, ConfigError) as exc:
        print(f"FAIL config parses: {exc}", file=stdout)
        return 1
    print("OK config parses", file=stdout)

    report = build_doctor_report(config)
    _print_bridge_name_checks(report.duplicate_bridge_names, stdout)
    _print_destination_checks(config, report.missing_default_destinations, stdout)
    _print_bridge_checks(report.bridge_discoveries, stdout)
    _print_skill_conflicts(report.duplicate_skill_names, stdout)
    _print_destination_classification(config, report.bridge_discoveries, stdout)
    return 1 if report.has_errors else 0


def _load_config(args: argparse.Namespace) -> Config:
    return load_config(args.config)


def _print_bridge_name_checks(duplicates: tuple[str, ...], stdout: TextIO) -> None:
    if duplicates:
        print(f"FAIL bridge names unique: {', '.join(duplicates)}", file=stdout)
    else:
        print("OK bridge names unique", file=stdout)


def _print_destination_checks(
    config: Config, missing_default_destinations: tuple[str, ...], stdout: TextIO
) -> None:
    names = list(config.ai_skill_paths)
    if len(names) == len(set(names)):
        print("OK destination names unique", file=stdout)
    else:
        print("FAIL destination names unique", file=stdout)
    if missing_default_destinations:
        print(
            "FAIL sync.default_destinations configured: "
            + ", ".join(missing_default_destinations),
            file=stdout,
        )
    else:
        print("OK sync.default_destinations configured", file=stdout)


def _print_bridge_checks(discoveries: tuple[BridgeDiscovery, ...], stdout: TextIO) -> None:
    for discovery in discoveries:
        bridge = discovery.bridge
        prefix = f"bridge {bridge.name}:"
        if not bridge.enabled:
            print(f"SKIP {prefix} disabled", file=stdout)
            if not discovery.root_exists:
                print(f"SKIP {prefix} disabled local path check: {bridge.path}", file=stdout)
            elif not discovery.root_is_dir:
                print(f"SKIP {prefix} disabled local path is not a directory: {bridge.path}", file=stdout)
            elif not discovery.exists:
                print(
                    f"SKIP {prefix} disabled skills_path check: {discovery.skills_dir}",
                    file=stdout,
                )
            else:
                print(
                    f"SKIP {prefix} disabled skill dir checks: {discovery.skills_dir}",
                    file=stdout,
                )
            if discovery.missing_skill_md:
                print(
                    f"SKIP {prefix} disabled skill dirs missing SKILL.md: "
                    f"{len(discovery.missing_skill_md)}",
                    file=stdout,
                )
            continue

        if not bridge.skills_path:
            print(f"FAIL {prefix} enabled bridge has skills_path", file=stdout)
        else:
            print(f"OK {prefix} enabled bridge has skills_path", file=stdout)

        if not discovery.root_exists:
            if bridge.repo:
                print(
                    f"WARN {prefix} local path missing but repo is cloneable: {bridge.path}",
                    file=stdout,
                )
            else:
                print(
                    f"FAIL {prefix} local path missing and no repo is configured: "
                    f"{bridge.path}",
                    file=stdout,
                )
        elif not discovery.root_is_dir:
            print(f"FAIL {prefix} local path is not a directory: {bridge.path}", file=stdout)
        elif discovery.exists:
            print(f"OK {prefix} local path exists: {bridge.path}", file=stdout)
            print(f"OK {prefix} local skills path exists: {discovery.skills_dir}", file=stdout)
        else:
            print(
                f"FAIL {prefix} local skills path missing under existing bridge root: "
                f"{discovery.skills_dir}",
                file=stdout,
            )

        for missing in discovery.missing_skill_md:
            print(f"FAIL {prefix} skill dir missing SKILL.md: {missing}", file=stdout)
        if discovery.exists and not discovery.missing_skill_md:
            print(f"OK {prefix} discovered skill dirs have SKILL.md", file=stdout)


def _print_skill_conflicts(
    conflicts: dict[str, tuple[object, ...]], stdout: TextIO
) -> None:
    if not conflicts:
        print("OK duplicate skill names across enabled bridges: none", file=stdout)
        return
    for name, skills in conflicts.items():
        locations = ", ".join(str(skill.path) for skill in skills)
        print(f"FAIL duplicate skill name {name}: {locations}", file=stdout)


def _print_destination_classification(
    config: Config, discoveries: tuple[BridgeDiscovery, ...], stdout: TextIO
) -> None:
    skills = enabled_skills(discoveries)
    if not config.ai_skill_paths:
        print("WARN no ai_skill_paths configured", file=stdout)
        return
    if not skills:
        print("WARN no enabled skills discovered for destination classification", file=stdout)
        return
    for dest_name, dest_path in sorted(config.ai_skill_paths.items()):
        print(f"Destination {dest_name}: {dest_path}", file=stdout)
        for skill in skills:
            status = classify_destination(dest_path, skill)
            print(f"  {skill.name}: {status.label} ({status.detail})", file=stdout)
