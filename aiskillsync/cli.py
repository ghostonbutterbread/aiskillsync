"""Command line interface for aiskillsync."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from urllib.parse import urlparse

from . import __version__
from .config import (
    BridgeConfig,
    Config,
    ConfigError,
    DEFAULT_CONFIG_TEXT,
    config_from_mapping,
    default_config_path,
    ensure_default_config,
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
from .sync import (
    SyncAction,
    SyncPlan,
    apply_sync_plan,
    build_sync_plan,
    materialize_repositories_for_sync,
)


DESTINATION_GROUPS = ("main", "codex", "claude", "ghost", "openclaw", "all")


@dataclass(frozen=True)
class SyncRequest:
    config: Config
    repo_selectors: tuple[str, ...]
    destinations: tuple[str, ...]
    notices: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


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

    sync_parser = subparsers.add_parser(
        "sync",
        help="plan or apply skill symlinks",
        description=(
            "Plan or apply skill symlinks. Preferred syntax is destination-first: "
            "sync main, sync codex --repo bounty-harness, sync openclaw --repo <url>. "
            "Legacy bridge-first syntax remains supported with --dest."
        ),
    )
    sync_parser.add_argument(
        "terms",
        nargs="*",
        help=(
            "destination groups (main, codex, claude, ghost/openclaw, all) "
            "or legacy bridge selectors"
        ),
    )
    sync_parser.add_argument(
        "--dest",
        action="append",
        default=[],
        help="legacy destination name to sync; repeat for multiple destinations",
    )
    sync_parser.add_argument(
        "--repo",
        action="append",
        default=[],
        help=(
            "configured bridge/repo name or repo URL to sync; repeat for multiple repos. "
            "Unconfigured URLs are cloned under the aiskillsync cache on apply."
        ),
    )
    mode_group = sync_parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="preview changes without mutating destination paths (default)",
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="create only missing destination symlinks",
    )
    sync_parser.set_defaults(func=cmd_sync)
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
    config_path = expand_path(args.config) if args.config else ensure_default_config()
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


def cmd_sync(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    config = _load_config(args)
    if config.sync.mode != "symlink":
        print(
            f"aiskillsync: unsupported sync.mode {config.sync.mode!r}; only 'symlink' is supported",
            file=stderr,
        )
        return 2

    request = _resolve_sync_request(config, args)
    dry_run = not args.apply
    materialization = materialize_repositories_for_sync(
        request.config,
        request.repo_selectors,
        dry_run=dry_run,
    )
    plan = build_sync_plan(
        request.config,
        request.repo_selectors,
        request.destinations,
        dry_run=dry_run,
        preflight_notices=(*request.notices, *materialization.notices),
        preflight_errors=(*request.errors, *materialization.errors),
    )
    _print_sync_plan(plan, stdout)

    if plan.has_blockers:
        print("Apply blocked by errors or conflicts", file=stdout)
        return 1
    if dry_run:
        print("Dry run only; pass --apply to create missing symlinks", file=stdout)
        return 0

    try:
        created = apply_sync_plan(plan)
    except (OSError, ValueError) as exc:
        print(f"aiskillsync: apply failed: {exc}", file=stderr)
        return 1
    if created:
        print("Created symlinks:", file=stdout)
        for item in created:
            print(f"  {item}", file=stdout)
    else:
        print("No symlinks needed", file=stdout)
    return 0


def _load_config(args: argparse.Namespace) -> Config:
    return load_config(args.config)


def _resolve_sync_request(config: Config, args: argparse.Namespace) -> SyncRequest:
    terms = tuple(args.terms)
    repo_options = tuple(args.repo)

    if args.dest:
        return _with_adhoc_repositories(
            config,
            (*terms, *repo_options) or ("all",),
            tuple(args.dest),
        )

    if terms == ("all",) and not repo_options:
        return _with_adhoc_repositories(config, ("all",), ())

    destination_terms: list[str] = []
    positional_repos: list[str] = []
    for term in terms:
        if term in DESTINATION_GROUPS:
            destination_terms.append(term)
        else:
            positional_repos.append(term)

    if destination_terms:
        destinations = _expand_destination_groups(config, tuple(destination_terms))
        repo_selectors = (*repo_options, *positional_repos) or ("all",)
        return _with_adhoc_repositories(config, repo_selectors, destinations)

    repo_selectors = (*repo_options, *terms) or ("all",)
    return _with_adhoc_repositories(config, repo_selectors, ())


def _expand_destination_groups(config: Config, groups: tuple[str, ...]) -> tuple[str, ...]:
    destinations: list[str] = []
    for group in groups:
        if group == "main":
            candidates = ("codex", "claude")
        elif group == "openclaw":
            candidates = ("ghost",) if "ghost" in config.ai_skill_paths else ("openclaw",)
        elif group == "ghost":
            candidates = ("ghost",) if "ghost" in config.ai_skill_paths else ("openclaw",)
        elif group == "all":
            candidates = tuple(config.ai_skill_paths)
        else:
            candidates = (group,)
        for candidate in candidates:
            if candidate not in destinations:
                destinations.append(candidate)
    return tuple(destinations)


def _with_adhoc_repositories(
    config: Config,
    selectors: tuple[str, ...],
    destinations: tuple[str, ...],
) -> SyncRequest:
    bridges = list(config.bridges)
    resolved_selectors: list[str] = []
    notices: list[str] = []

    for selector in selectors:
        if not _looks_like_repo_url(selector) or _configured_repo_url(config, selector):
            resolved_selectors.append(selector)
            continue

        bridge = _adhoc_bridge_for_url(selector)
        bridges.append(bridge)
        resolved_selectors.append(bridge.name)
        notices.append(
            f"ADHOC bridge {bridge.name}: {bridge.repo} -> {bridge.path}"
        )

    if len(bridges) == len(config.bridges):
        resolved_config = config
    else:
        resolved_config = Config(
            path=config.path,
            bridges=tuple(bridges),
            ai_skill_paths=config.ai_skill_paths,
            sync=config.sync,
        )
    return SyncRequest(
        config=resolved_config,
        repo_selectors=tuple(resolved_selectors),
        destinations=destinations,
        notices=tuple(notices),
    )


def _configured_repo_url(config: Config, repo_url: str) -> bool:
    normalized = _normalize_repo_url(repo_url)
    return any(
        bridge.repo is not None and _normalize_repo_url(bridge.repo) == normalized
        for bridge in config.bridges
    )


def _looks_like_repo_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https", "ssh", "git", "file"}:
        return True
    return "@" in value and ":" in value


def _normalize_repo_url(value: str) -> str:
    return value.strip().rstrip("/")


def _adhoc_bridge_for_url(repo_url: str) -> BridgeConfig:
    normalized = _normalize_repo_url(repo_url)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    slug = _repo_slug(normalized)
    name = f"{slug}-{digest}"
    return BridgeConfig(
        name=name,
        repo=repo_url,
        path=_default_repo_cache_dir() / name,
        skills_path="skills",
        branch=None,
        enabled=True,
    )


def _repo_slug(repo_url: str) -> str:
    parsed = urlparse(repo_url)
    tail = Path(parsed.path).name if parsed.path else repo_url.rsplit("/", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    slug = "".join(char.lower() if char.isalnum() else "-" for char in tail).strip("-")
    return slug or "repo"


def _default_repo_cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME")
    if root:
        return expand_path(root) / "aiskillsync" / "repos"
    return expand_path("~/.cache/aiskillsync/repos")


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


def _print_sync_plan(plan: SyncPlan, stdout: TextIO) -> None:
    mode = "dry-run" if plan.dry_run else "apply"
    print(f"Sync plan ({mode})", file=stdout)
    if plan.selected_discoveries:
        bridges = ", ".join(item.bridge.name for item in plan.selected_discoveries)
    else:
        bridges = "-"
    print(f"Bridges: {bridges}", file=stdout)
    print(
        "Destinations: " + (", ".join(plan.destinations) if plan.destinations else "-"),
        file=stdout,
    )
    for notice in plan.notices:
        print(notice, file=stdout)
    for error in plan.errors:
        print(f"ERROR {error}", file=stdout)
    for name, skills in plan.duplicate_skills.items():
        locations = ", ".join(str(skill.path) for skill in skills)
        print(f"ERROR duplicate selected skill name {name}: {locations}", file=stdout)

    if not plan.actions:
        print("No destination actions", file=stdout)
        return

    print("Destination actions:", file=stdout)
    for action in plan.actions:
        print(f"  {_format_sync_action(action)}", file=stdout)


def _format_sync_action(action: SyncAction) -> str:
    if action.action == "link":
        verb = "LINK"
    elif action.action == "skip":
        verb = "SKIP"
    else:
        verb = "CONFLICT"
    return (
        f"{verb} {action.destination}:{action.skill.name} "
        f"{action.status.label} ({action.status.detail})"
    )
