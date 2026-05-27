"""Command line interface for aiskillsync."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import os
import subprocess
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
    DEFAULT_REPO_DIR,
    _format_scalar,
    _split_key_value,
    _strip_inline_comment,
    atomic_write_text,
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
    make_backup_root,
    materialize_repositories_for_sync,
    select_bridge_configs,
)


DESTINATION_GROUPS = ("main", "codex", "claude", "ghost", "openclaw", "all")


@dataclass(frozen=True)
class SyncRequest:
    config: Config
    repo_selectors: tuple[str, ...]
    destinations: tuple[str, ...]
    notices: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncRepoPreflight:
    notices: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    skip_clone_bridges: tuple[str, ...] = ()
    skip_pull_bridges: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConfigTextBlock:
    start: int
    end: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aiskillsync",
        description="Sync AI skill repos into provider skill paths.",
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

    list_parser = subparsers.add_parser("list", help="show repos and discovered skills")
    list_parser.set_defaults(func=cmd_list)

    doctor_parser = subparsers.add_parser("doctor", help="validate config and filesystem state")
    doctor_parser.set_defaults(func=cmd_doctor)

    sync_parser = subparsers.add_parser(
        "sync",
        help="apply or preview skill symlinks",
        description=(
            "Apply or preview skill symlinks. Preferred syntax is destination-first: "
            "sync main, sync codex --repo bounty-harness, sync openclaw --repo <url>. "
            "Legacy repo-first selector syntax remains supported with --dest."
        ),
    )
    sync_parser.add_argument(
        "terms",
        nargs="*",
        help=(
            "destination groups (main, codex, claude, ghost/openclaw, all) "
            "or legacy repo selectors"
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
            "configured repo name or repo URL to sync; repeat for multiple repos. "
            "Unconfigured URLs are cloned under repo_dir when applying."
        ),
    )
    sync_parser.add_argument(
        "--skill",
        action="append",
        default=[],
        help="only sync/adopt this skill name; repeat for multiple skills",
    )
    sync_parser.add_argument(
        "--exclude-skill",
        action="append",
        default=[],
        help="skip this skill name during sync/adoption; repeat for multiple skills",
    )
    sync_parser.add_argument(
        "--denylist",
        action="append",
        type=Path,
        default=[],
        help="file of skill names to skip during sync/adoption; comments and blank lines ignored",
    )
    sync_parser.add_argument(
        "--adopt",
        action="store_true",
        help=(
            "opt-in migration mode: back up existing same-name destination entries "
            "and replace them with symlinks to the selected repo skills"
        ),
    )
    sync_parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help=(
            "increase sync output detail; -v shows skipped/no-op destination actions, "
            "-vv also shows file diffs for comparable skill directories"
        ),
    )
    mode_group = sync_parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="preview changes without cloning, pulling, or mutating destination paths",
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="apply sync changes (accepted for compatibility; sync applies by default)",
    )
    sync_parser.set_defaults(func=cmd_sync)

    repo_parser = subparsers.add_parser("repo", help="manage configured repos")
    repo_subparsers = repo_parser.add_subparsers(dest="repo_command", required=True)

    repo_add_parser = repo_subparsers.add_parser("add", help="add a repo to config")
    repo_add_parser.add_argument("repo", metavar="repo-or-path", help="repo URL or local path to add")
    repo_add_parser.add_argument(
        "location",
        nargs="?",
        type=Path,
        help="local checkout path for URL repos (default: repo_dir/<name>)",
    )
    repo_add_parser.add_argument("--name", help="configured repo name (default: repo slug or path basename)")
    repo_add_parser.add_argument(
        "--path",
        dest="path",
        type=Path,
        help=argparse.SUPPRESS,
    )
    repo_add_parser.add_argument(
        "--skills-path",
        default="skills",
        help="skills directory inside the repo (default: skills)",
    )
    repo_add_parser.add_argument("--branch", help="branch to clone when missing")
    repo_add_parser.add_argument(
        "--disabled",
        action="store_true",
        help="add the repo disabled",
    )
    repo_add_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview the config change without writing",
    )
    repo_add_parser.set_defaults(func=cmd_repo_add)

    repo_remove_parser = repo_subparsers.add_parser("remove", help="remove a repo from config")
    repo_remove_parser.add_argument("repo", help="repo name, index, or URL to remove")
    repo_remove_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview the config change without writing",
    )
    repo_remove_parser.set_defaults(func=cmd_repo_remove)

    add_parser = subparsers.add_parser("add", help="add a repo to config")
    add_parser.add_argument("repo", metavar="repo-or-path", help="repo URL or local path to add")
    add_parser.add_argument(
        "location",
        nargs="?",
        type=Path,
        help="local checkout path for URL repos (default: repo_dir/<name>)",
    )
    add_parser.add_argument("--name", help="configured repo name (default: repo slug or path basename)")
    add_parser.add_argument("--path", type=Path, help=argparse.SUPPRESS)
    add_parser.add_argument(
        "--skills-path",
        default="skills",
        help="skills directory inside the repo (default: skills)",
    )
    add_parser.add_argument("--branch", help="branch to clone when missing")
    add_parser.add_argument("--disabled", action="store_true", help="add the repo disabled")
    add_parser.add_argument("--dry-run", action="store_true", help="preview the config change without writing")
    add_parser.set_defaults(func=cmd_repo_add)

    remove_parser = subparsers.add_parser("remove", help="remove a repo from config")
    remove_parser.add_argument("repo", help="repo name, index, or URL to remove")
    remove_parser.add_argument("--dry-run", action="store_true", help="preview the config change without writing")
    remove_parser.set_defaults(func=cmd_repo_remove)
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

    atomic_write_text(config_path, DEFAULT_CONFIG_TEXT)
    print(f"Wrote config: {config_path}", file=stdout)
    return 0


def cmd_config(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    if args.show_default:
        print(DEFAULT_CONFIG_TEXT.rstrip(), file=stdout)
        return 0

    config = _load_config(args)
    print(f"Config: {config.path}", file=stdout)
    print("", file=stdout)
    print(f"Repo directory: {config.repo_dir}", file=stdout)
    print("", file=stdout)
    print("Repos:", file=stdout)
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
    print(
        "  migration_denylist: "
        + (", ".join(config.sync.migration_denylist) if config.sync.migration_denylist else "-"),
        file=stdout,
    )
    return 0


def cmd_list(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    config = _load_config(args)
    discoveries = discover_bridges(config)
    skills = enabled_skills(discoveries)
    summary = destination_summary(config.ai_skill_paths, skills)

    print("Repos:", file=stdout)
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


def cmd_repo_add(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    config = _load_config(args)
    location = getattr(args, "location", None)
    path_alias = args.path
    if location is not None and path_alias is not None:
        print("aiskillsync: use either positional location or --path, not both", file=stderr)
        return 2

    source_is_local_path = _looks_like_local_path(args.repo)
    if source_is_local_path and (location is not None or path_alias is not None):
        print("aiskillsync: local path repos use the first argument as their path", file=stderr)
        return 2

    source_path = expand_path(args.repo) if source_is_local_path else None
    name = args.name or (
        _repo_name_from_path(source_path) if source_path is not None else _repo_slug(args.repo)
    )
    if not _valid_repo_name(name):
        print(f"aiskillsync: invalid repo name: {name!r}", file=stderr)
        return 2
    if not args.skills_path:
        print("aiskillsync: --skills-path must be non-empty", file=stderr)
        return 2

    location_path = path_alias if path_alias is not None else location
    config_text = _read_config_text(config.path)
    path_literal = _repo_add_path_literal(config_text, args.repo, source_path, location_path, name)
    path = source_path or expand_path(path_literal)
    new_repo = BridgeConfig(
        name=name,
        repo=None if source_path is not None else args.repo,
        path=path,
        skills_path=args.skills_path,
        branch=args.branch,
        enabled=not args.disabled,
    )

    errors = _repo_add_conflicts(config, new_repo)
    if errors:
        for error in errors:
            print(f"aiskillsync: {error}", file=stderr)
        return 1

    state = "enabled" if new_repo.enabled else "disabled"
    if args.dry_run:
        print(
            f"Would add repo {new_repo.name} ({state}): "
            f"{_repo_source_label(new_repo)} -> {new_repo.path}",
            file=stdout,
        )
        return 0

    _write_config_bridge_add(config.path, config_text, new_repo, path_literal)
    print(f"Added repo {new_repo.name} ({state}): {_repo_source_label(new_repo)}", file=stdout)
    print(f"Local path: {new_repo.path}", file=stdout)
    print("No repo directory was cloned or modified", file=stdout)
    return 0


def cmd_repo_remove(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    config = _load_config(args)
    repo, index, errors = _select_configured_repo_with_index(config, args.repo)
    if errors:
        for error in errors:
            print(f"aiskillsync: {error}", file=stderr)
        return 1

    if args.dry_run:
        print(f"Would remove repo {repo.name} from config", file=stdout)
        print(f"Local path would be left untouched: {repo.path}", file=stdout)
        return 0

    _write_config_bridge_remove(config.path, _read_config_text(config.path), index)
    print(f"Removed repo {repo.name} from config", file=stdout)
    print(f"Left local path untouched: {repo.path}", file=stdout)
    return 0


def cmd_sync(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    config = _load_config(args)
    if config.sync.mode != "symlink":
        print(
            f"aiskillsync: unsupported sync.mode {config.sync.mode!r}; only 'symlink' is supported",
            file=stderr,
        )
        return 2

    request = _resolve_sync_request(config, args)
    dry_run = args.dry_run
    denylist, denylist_errors = _sync_denylist(config, args)
    backup_root = make_backup_root() if args.adopt else None
    repo_preflight = _sync_repo_preflight(
        request.config,
        request.repo_selectors,
        dry_run=dry_run,
        stdin=sys.stdin,
        stdout=stdout,
    )
    materialization = materialize_repositories_for_sync(
        request.config,
        request.repo_selectors,
        dry_run=dry_run,
        skip_clone_bridges=frozenset(repo_preflight.skip_clone_bridges),
        skip_pull_bridges=frozenset(repo_preflight.skip_pull_bridges),
    )
    plan = build_sync_plan(
        request.config,
        request.repo_selectors,
        request.destinations,
        dry_run=dry_run,
        adopt=args.adopt,
        include_skills=tuple(args.skill),
        exclude_skills=denylist,
        preflight_notices=(*request.notices, *repo_preflight.notices, *materialization.notices),
        preflight_errors=(
            *request.errors,
            *denylist_errors,
            *repo_preflight.errors,
            *materialization.errors,
        ),
        skipped_missing_repos=materialization.skipped_missing_roots,
        backup_root=backup_root,
    )
    _print_sync_plan(plan, stdout, verbosity=args.verbose)

    if plan.has_blockers:
        print(_colorize("Apply blocked by errors or conflicts", "red", stdout), file=stdout)
        _print_sync_summary(plan, "blocked", stdout)
        return 1
    if dry_run:
        print(_colorize("Dry run only; no filesystem changes were made", "blue", stdout), file=stdout)
        _print_sync_summary(plan, "dry-run", stdout)
        return 0

    try:
        created = apply_sync_plan(plan)
    except (OSError, ValueError) as exc:
        print(f"aiskillsync: apply failed: {exc}", file=stderr)
        _print_sync_summary(plan, "blocked", stdout)
        return 1
    if created:
        print("Created symlinks:", file=stdout)
        for item in created:
            print(f"  {item}", file=stdout)
        if plan.backup_root is not None and plan.adoptions:
            print(f"Backups written to: {plan.backup_root}", file=stdout)
    else:
        print("No symlinks needed", file=stdout)
    _print_sync_summary(plan, "applied", stdout)
    return 0


def _load_config(args: argparse.Namespace) -> Config:
    return load_config(args.config)


def _sync_denylist(
    config: Config, args: argparse.Namespace
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    names: list[str] = [*args.exclude_skill]
    if args.adopt:
        names.extend(config.sync.migration_denylist)
    errors: list[str] = []
    for path in args.denylist:
        try:
            names.extend(_read_skill_denylist(path))
        except OSError as exc:
            errors.append(f"could not read denylist {path}: {exc}")
    return tuple(dict.fromkeys(names)), tuple(errors)


def _read_skill_denylist(path: Path) -> tuple[str, ...]:
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name = stripped.split("#", 1)[0].strip()
        if name:
            names.append(name)
    return tuple(names)


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


def _sync_repo_preflight(
    config: Config,
    selectors: tuple[str, ...],
    *,
    dry_run: bool,
    stdin: TextIO,
    stdout: TextIO,
) -> SyncRepoPreflight:
    if dry_run:
        return SyncRepoPreflight()

    selected, selection_errors = select_bridge_configs(config.bridges, selectors)
    if selection_errors:
        return SyncRepoPreflight()

    notices: list[str] = []
    skip_clone_bridges: list[str] = []
    skip_pull_bridges: list[str] = []
    auth_by_host: dict[str, bool] = {}

    for bridge in selected:
        if not bridge.enabled or bridge.repo is None:
            continue

        action = _repo_git_sync_action(config, bridge)
        if action is None:
            continue

        auth_url = _repo_auth_url(bridge, action)
        if not _repo_url_uses_ssh(auth_url):
            continue

        host = _github_repo_host(auth_url)
        if host is None:
            continue

        authenticated = auth_by_host.get(host)
        if authenticated is None:
            authenticated = _github_ssh_auth_configured(host)
            auth_by_host[host] = authenticated
        if authenticated:
            continue

        if _interactive_prompt_available(stdin, stdout):
            if _confirm_github_sync_continue(bridge.name, host, action, stdin, stdout):
                notices.append(
                    f"WARN repo {bridge.name}: GitHub auth not detected for {host} before {action}; continuing by user choice"
                )
            elif action == "clone":
                skip_clone_bridges.append(bridge.name)
            else:
                skip_pull_bridges.append(bridge.name)
            continue

        notices.append(
            f"WARN repo {bridge.name}: GitHub auth not detected for {host} before {action}; non-interactive mode will continue"
        )

    return SyncRepoPreflight(
        notices=tuple(notices),
        skip_clone_bridges=tuple(skip_clone_bridges),
        skip_pull_bridges=tuple(skip_pull_bridges),
    )


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
    errors: list[str] = []

    for selector in selectors:
        if not _looks_like_repo_url(selector) or _configured_repo_url(config, selector):
            resolved_selectors.append(selector)
            continue

        bridge = _adhoc_bridge_for_url(config, selector)
        path_owner = _configured_repo_by_path(config, bridge.path)
        if path_owner is not None:
            errors.append(
                f"repo URL {selector} resolves to {bridge.path}, already used by "
                f"configured repo {path_owner.name}"
            )
            resolved_selectors.append(bridge.name)
            continue
        bridges.append(bridge)
        resolved_selectors.append(bridge.name)
        notices.append(
            f"ADHOC repo {bridge.name}: {bridge.repo} -> {bridge.path}"
        )

    if len(bridges) == len(config.bridges):
        resolved_config = config
    else:
        resolved_config = Config(
            path=config.path,
            repo_dir=config.repo_dir,
            bridges=tuple(bridges),
            ai_skill_paths=config.ai_skill_paths,
            sync=config.sync,
        )
    return SyncRequest(
        config=resolved_config,
        repo_selectors=tuple(resolved_selectors),
        destinations=destinations,
        notices=tuple(notices),
        errors=tuple(errors),
    )


def _read_config_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read config {path}: {exc}") from exc


def _repo_add_path_literal(
    config_text: str,
    repo_arg: str,
    source_path: Path | None,
    location_path: Path | None,
    name: str,
) -> str:
    if source_path is not None:
        return repo_arg
    if location_path is not None:
        return str(location_path)
    repo_dir_literal = _top_level_string_value(config_text, "repo_dir")
    if repo_dir_literal is None:
        repo_dir_literal = str(DEFAULT_REPO_DIR)
    return str(Path(repo_dir_literal) / name)


def _top_level_string_value(text: str, key: str) -> str | None:
    raw = parse_simple_yaml(text)
    if not isinstance(raw, dict):
        return None
    value = raw.get(key)
    return value if isinstance(value, str) and value else None


def _write_config_bridge_add(
    path: Path, original_text: str, bridge: BridgeConfig, path_literal: str
) -> None:
    try:
        updated = _add_bridge_entry_text(original_text, bridge, path_literal)
        atomic_write_text(path, updated)
    except OSError as exc:
        raise ConfigError(f"could not write config {path}: {exc}") from exc


def _write_config_bridge_remove(path: Path, original_text: str, bridge_index: int) -> None:
    try:
        updated = _remove_bridge_entry_text(original_text, bridge_index)
        atomic_write_text(path, updated)
    except OSError as exc:
        raise ConfigError(f"could not write config {path}: {exc}") from exc


def _add_bridge_entry_text(text: str, bridge: BridgeConfig, path_literal: str) -> str:
    text = _ensure_repo_dir_text(text)
    lines = text.splitlines(keepends=True)
    newline = _preferred_newline(text)
    block = _find_top_level_block(lines, "bridges")
    if block is None:
        prefix = text
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += newline
        if prefix and prefix.strip():
            prefix += newline
        return prefix + "bridges:" + newline + _bridge_entry_text(
            bridge, path_literal, newline, "  "
        )

    indent = _bridge_item_indent(lines, block) or "  "
    entry = _bridge_entry_text(bridge, path_literal, newline, indent).splitlines(
        keepends=True
    )
    insert_at = _bridge_insert_index(lines, block)
    if _top_level_value_is_empty_list(lines[block.start]):
        lines[block.start] = _set_key_line_value(lines[block.start], "")
        insert_at = block.start + 1
    lines[insert_at:insert_at] = entry
    return "".join(lines)


def _ensure_repo_dir_text(text: str) -> str:
    if _top_level_string_value(text, "repo_dir") is not None:
        return text
    lines = text.splitlines(keepends=True)
    newline = _preferred_newline(text)
    insert_at = 0
    while insert_at < len(lines) and _logical_line(lines[insert_at]) is None:
        insert_at += 1
    lines[insert_at:insert_at] = [f"repo_dir: {DEFAULT_REPO_DIR}{newline}", newline]
    return "".join(lines)


def _remove_bridge_entry_text(text: str, bridge_index: int) -> str:
    lines = text.splitlines(keepends=True)
    block = _find_top_level_block(lines, "bridges")
    if block is None:
        raise ConfigError("config has no bridges block")
    spans = _bridge_item_spans(lines, block)
    if bridge_index < 0 or bridge_index >= len(spans):
        raise ConfigError(f"could not locate bridges[{bridge_index + 1}] in config text")

    if len(spans) == 1:
        updated = [*lines[: block.start + 1], *lines[block.end :]]
        updated[block.start] = _set_key_line_value(lines[block.start], "[]")
        return "".join(updated)

    remove_start, remove_end = spans[bridge_index]
    return "".join([*lines[:remove_start], *lines[remove_end:]])


def _find_top_level_block(lines: list[str], key: str) -> ConfigTextBlock | None:
    start: int | None = None
    for index, line in enumerate(lines):
        logical = _logical_line(line)
        if logical is None:
            continue
        indent, content = logical
        key_value = _split_key_value(content)
        if indent == 0 and key_value is not None and key_value[0] == key:
            start = index
            break
    if start is None:
        return None

    end = len(lines)
    for index in range(start + 1, len(lines)):
        logical = _logical_line(lines[index])
        if logical is None:
            continue
        indent, content = logical
        if indent == 0 and _split_key_value(content) is not None:
            end = index
            break
    return ConfigTextBlock(start=start, end=end)


def _bridge_item_spans(
    lines: list[str], block: ConfigTextBlock
) -> list[tuple[int, int]]:
    candidates: list[tuple[int, int]] = []
    for index in range(block.start + 1, block.end):
        logical = _logical_line(lines[index])
        if logical is None:
            continue
        indent, content = logical
        if indent > 0 and content.startswith("- "):
            candidates.append((index, indent))

    if not candidates:
        return []
    item_indent = min(indent for _, indent in candidates)
    starts = [index for index, indent in candidates if indent == item_indent]

    spans: list[tuple[int, int]] = []
    content_end = _bridge_content_end(lines, block)
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else content_end
        spans.append((start, end))
    return spans


def _bridge_item_indent(lines: list[str], block: ConfigTextBlock) -> str | None:
    spans = _bridge_item_spans(lines, block)
    if not spans:
        return None
    line = lines[spans[0][0]]
    return line[: len(line) - len(line.lstrip(" "))]


def _bridge_insert_index(lines: list[str], block: ConfigTextBlock) -> int:
    return _bridge_content_end(lines, block)


def _bridge_content_end(lines: list[str], block: ConfigTextBlock) -> int:
    insert_at = block.end
    while insert_at > block.start + 1 and _is_blank_or_comment(lines[insert_at - 1]):
        insert_at -= 1
    return insert_at


def _bridge_entry_text(
    bridge: BridgeConfig, path_literal: str, newline: str, item_indent: str
) -> str:
    child_indent = item_indent + "  "
    lines = [
        f"{item_indent}- name: {_format_scalar(bridge.name)}",
    ]
    if bridge.repo is not None:
        lines.append(f"{child_indent}repo: {_format_scalar(bridge.repo)}")
    lines.extend(
        [
            f"{child_indent}path: {_format_scalar(path_literal)}",
            f"{child_indent}skills_path: {_format_scalar(bridge.skills_path)}",
        ]
    )
    if bridge.branch is not None:
        lines.append(f"{child_indent}branch: {_format_scalar(bridge.branch)}")
    lines.append(f"{child_indent}enabled: {str(bridge.enabled).lower()}")
    return newline.join(lines) + newline


def _logical_line(line: str) -> tuple[int, str] | None:
    body = line.rstrip("\r\n").rstrip()
    without_comment = _strip_inline_comment(body)
    if not without_comment.strip():
        return None
    indent = len(without_comment) - len(without_comment.lstrip(" "))
    return indent, without_comment.strip()


def _is_blank_or_comment(line: str) -> bool:
    return _logical_line(line) is None


def _top_level_value_is_empty_list(line: str) -> bool:
    logical = _logical_line(line)
    if logical is None:
        return False
    _, content = logical
    key_value = _split_key_value(content)
    return key_value is not None and key_value[1] == "[]"


def _set_key_line_value(line: str, value: str) -> str:
    body = line.rstrip("\r\n")
    newline = line[len(body) :]
    logical = _strip_inline_comment(body)
    comment = body[len(logical) :]
    key = logical.split(":", 1)[0].rstrip()
    if value:
        updated = f"{key}: {value}"
    else:
        updated = f"{key}:"
    if comment:
        updated = f"{updated} {comment.lstrip()}"
    return updated + newline


def _preferred_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


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


def _looks_like_local_path(value: str) -> bool:
    if _looks_like_repo_url(value):
        return False
    path = expand_path(value)
    if path.exists():
        return True
    if value.startswith(("~", ".", "/", "\\")):
        return True
    if "/" in value or "\\" in value:
        return True
    return bool(Path(value).suffix)


def _normalize_repo_url(value: str) -> str:
    return value.strip().rstrip("/")


def _adhoc_bridge_for_url(config: Config, repo_url: str) -> BridgeConfig:
    normalized = _normalize_repo_url(repo_url)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    slug = _repo_slug(normalized)
    name = f"{slug}-{digest}"
    return BridgeConfig(
        name=name,
        repo=repo_url,
        path=config.repo_dir / name,
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


def _repo_name_from_path(path: Path) -> str:
    name = path.name
    if name in {"", ".", ".."}:
        name = _canonical_path(path).name
    return name


def _repo_source_label(repo: BridgeConfig) -> str:
    return repo.repo if repo.repo is not None else "local path"


def _configured_repo_by_path(config: Config, path: Path) -> BridgeConfig | None:
    canonical_path = _canonical_path(path)
    for bridge in config.bridges:
        if _canonical_path(bridge.path) == canonical_path:
            return bridge
    return None


def _repo_git_sync_action(config: Config, bridge: BridgeConfig) -> str | None:
    root_exists = bridge.path.exists()
    if not root_exists:
        if bridge.repo and config.sync.clone_if_missing:
            return "clone"
        return None
    if not bridge.path.is_dir():
        return None
    if config.sync.pull_before_sync:
        return "pull"
    return None


def _github_repo_host(repo_url: str) -> str | None:
    parsed = urlparse(repo_url)
    host = parsed.hostname
    if host is None and "://" not in repo_url and "@" in repo_url and ":" in repo_url:
        host = repo_url.rsplit("@", 1)[1].split(":", 1)[0]
    if host is None:
        return None
    host = host.lower()
    if host == "github.com" or host.startswith("github."):
        return host
    return None


def _repo_auth_url(bridge: BridgeConfig, action: str) -> str:
    if action == "pull":
        remote_url = _git_origin_url(bridge.path)
        if remote_url:
            return remote_url
    return bridge.repo or ""


def _git_origin_url(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    remote = result.stdout.strip()
    return remote or None


def _repo_url_uses_ssh(repo_url: str) -> bool:
    parsed = urlparse(repo_url)
    if parsed.scheme in {"ssh", "git+ssh"}:
        return True
    return "://" not in repo_url and "@" in repo_url and ":" in repo_url


def _github_ssh_auth_configured(host: str) -> bool:
    try:
        result = subprocess.run(
            [
                "ssh",
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=accept-new",
                f"git@{host}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError:
        return False
    output = f"{result.stdout}\n{result.stderr}".lower()
    return "successfully authenticated" in output


def _interactive_prompt_available(stdin: TextIO, stdout: TextIO) -> bool:
    stdin_isatty = getattr(stdin, "isatty", None)
    stdout_isatty = getattr(stdout, "isatty", None)
    return bool(stdin_isatty and stdin_isatty() and stdout_isatty and stdout_isatty())


def _confirm_github_sync_continue(
    repo_name: str,
    host: str,
    action: str,
    stdin: TextIO,
    stdout: TextIO,
) -> bool:
    prompt = (
        f"GitHub auth not detected for repo {repo_name} on {host} before {action}. "
        "Continue anyway? [Y/n] "
    )
    print(prompt, end="", file=stdout, flush=True)
    answer = stdin.readline()
    if not answer:
        print("", file=stdout)
        return True
    print("", file=stdout)
    return answer.strip().lower() not in {"n", "no"}


def _repo_add_conflicts(config: Config, repo: BridgeConfig) -> tuple[str, ...]:
    errors: list[str] = []
    if any(bridge.name == repo.name for bridge in config.bridges):
        errors.append(f"repo name already configured: {repo.name}")
    if any(
        bridge.repo is not None
        and repo.repo is not None
        and _normalize_repo_url(bridge.repo) == _normalize_repo_url(repo.repo)
        for bridge in config.bridges
    ):
        errors.append(f"repo URL already configured: {repo.repo}")
    path_owner = _configured_repo_by_path(config, repo.path)
    if path_owner is not None:
        errors.append(f"local path already used by repo {path_owner.name}: {repo.path}")
    if repo.path.exists() and not repo.path.is_dir():
        errors.append(f"local path exists and is not a directory: {repo.path}")
    return tuple(errors)


def _select_configured_repo(
    config: Config, selector: str
) -> tuple[BridgeConfig | None, tuple[str, ...]]:
    repo, _, errors = _select_configured_repo_with_index(config, selector)
    return repo, errors


def _select_configured_repo_with_index(
    config: Config, selector: str
) -> tuple[BridgeConfig | None, int, tuple[str, ...]]:
    if selector.isdigit():
        index = int(selector)
        if index < 1 or index > len(config.bridges):
            return None, -1, (f"repo index out of range: {selector}",)
        return config.bridges[index - 1], index - 1, ()

    matches = [
        (index, bridge)
        for index, bridge in enumerate(config.bridges)
        if bridge.name == selector
        or (
            bridge.repo is not None
            and _normalize_repo_url(bridge.repo) == _normalize_repo_url(selector)
        )
    ]
    if not matches:
        return None, -1, (f"unknown repo: {selector}",)
    if len(matches) > 1:
        return None, -1, (f"ambiguous repo: {selector}",)
    index, bridge = matches[0]
    return bridge, index, ()


def _valid_repo_name(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    return all(char.isalnum() or char in {"-", "_", "."} for char in value)


def _canonical_path(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def _print_bridge_name_checks(duplicates: tuple[str, ...], stdout: TextIO) -> None:
    if duplicates:
        print(f"FAIL repo names unique: {', '.join(duplicates)}", file=stdout)
    else:
        print("OK repo names unique", file=stdout)


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
        prefix = f"repo {bridge.name}:"
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
            print(f"FAIL {prefix} enabled repo has skills_path", file=stdout)
        else:
            print(f"OK {prefix} enabled repo has skills_path", file=stdout)

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
                f"WARN {prefix} local skills path missing under existing repo root: "
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
        print("OK duplicate skill names across enabled repos: none", file=stdout)
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


def _print_sync_plan(plan: SyncPlan, stdout: TextIO, *, verbosity: int = 0) -> None:
    mode = "dry-run" if plan.dry_run else "apply"
    print(f"Sync plan ({mode})", file=stdout)
    if plan.selected_discoveries:
        bridges = ", ".join(item.bridge.name for item in plan.selected_discoveries)
    else:
        bridges = "-"
    print(f"Repos: {bridges}", file=stdout)
    print(
        "Destinations: " + (", ".join(plan.destinations) if plan.destinations else "-"),
        file=stdout,
    )
    for notice in plan.notices:
        print(_colorize_notice(notice, stdout), file=stdout)
    for error in plan.errors:
        print(_colorize(f"ERROR {error}", "red", stdout), file=stdout)
    for name, skills in plan.duplicate_skills.items():
        locations = ", ".join(str(skill.path) for skill in skills)
        print(
            _colorize(f"ERROR duplicate selected skill name {name}: {locations}", "red", stdout),
            file=stdout,
        )
    if plan.backup_root is not None and plan.adoptions:
        verb = "PLAN backup root" if plan.dry_run else "BACKUP root"
        print(_colorize(f"{verb}: {plan.backup_root}", "blue", stdout), file=stdout)

    visible_actions = _visible_sync_actions(plan, verbosity=verbosity)
    hidden_skips = len(plan.actions) - len(visible_actions)

    if not plan.actions:
        print("No destination actions", file=stdout)
        return

    if not visible_actions:
        print(
            f"No destination actions to show ({hidden_skips} skipped; use --verbose to show no-ops)",
            file=stdout,
        )
        return

    suffix = (
        f" ({hidden_skips} skipped hidden; use --verbose to show no-ops)"
        if hidden_skips
        else ""
    )
    print(f"Destination actions:{suffix}", file=stdout)
    for action in visible_actions:
        print(f"  {_format_sync_action(action, stdout, dry_run=plan.dry_run)}", file=stdout)

    if verbosity >= 2:
        _print_sync_diffs(plan, stdout)


def _visible_sync_actions(plan: SyncPlan, *, verbosity: int) -> tuple[SyncAction, ...]:
    if verbosity >= 1:
        return plan.actions
    return tuple(action for action in plan.actions if action.action != "skip")


def _print_sync_diffs(plan: SyncPlan, stdout: TextIO) -> None:
    diffs = tuple(_iter_sync_diffs(plan))
    if not diffs:
        print("File diffs: none", file=stdout)
        return

    print("File diffs:", file=stdout)
    for line in diffs:
        print(line, file=stdout)


def _iter_sync_diffs(plan: SyncPlan) -> tuple[str, ...]:
    lines: list[str] = []
    for action in plan.actions:
        destination_path = _diffable_destination_path(action)
        if destination_path is None:
            continue
        diff = _skill_directory_diff(action.skill.path, destination_path)
        if not diff:
            continue
        lines.append(f"--- {action.destination}:{action.skill.name}")
        lines.extend(diff)
    return tuple(lines)


def _diffable_destination_path(action: SyncAction) -> Path | None:
    path = action.status.path
    if path.is_symlink():
        resolved = path.resolve()
        return resolved if resolved.is_dir() else None
    if path.is_dir():
        return path
    return None


def _skill_directory_diff(source: Path, destination: Path) -> tuple[str, ...]:
    source_files = _relative_text_file_paths(source)
    destination_files = _relative_text_file_paths(destination)
    diff_lines: list[str] = []
    for relative in sorted(source_files | destination_files):
        source_file = source / relative
        destination_file = destination / relative
        source_text = _read_text_for_diff(source_file)
        destination_text = _read_text_for_diff(destination_file)
        if source_text == destination_text:
            continue
        if source_text is None or destination_text is None:
            diff_lines.append(f"Binary or unreadable file differs: {relative}")
            continue
        diff_lines.extend(
            difflib.unified_diff(
                source_text.splitlines(),
                destination_text.splitlines(),
                fromfile=str(source_file),
                tofile=str(destination_file),
                lineterm="",
            )
        )
    return tuple(diff_lines)


def _relative_text_file_paths(root: Path) -> set[Path]:
    if not root.is_dir():
        return set()
    return {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def _read_text_for_diff(path: Path) -> str | None:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    except OSError:
        return None


def _format_sync_action(
    action: SyncAction, stdout: TextIO | None = None, *, dry_run: bool = False
) -> str:
    if action.action == "link":
        verb = "LINK"
        color = "blue" if dry_run else "green"
    elif action.action == "adopt":
        verb = "ADOPT"
        color = "blue" if dry_run else "green"
    elif action.action == "skip":
        verb = "SKIP"
        color = "yellow"
    else:
        verb = "CONFLICT"
        color = "red"
    return (
        f"{_colorize(verb, color, stdout)} {action.destination}:{action.skill.name} "
        f"{action.status.label} ({action.status.detail})"
    )


def _print_sync_summary(plan: SyncPlan, final_status: str, stdout: TextIO) -> None:
    repo_counts = _repo_summary_counts(plan)
    destination_counts = _destination_summary_counts(plan)
    status_color = {
        "applied": "green",
        "dry-run": "blue",
        "blocked": "red",
    }.get(final_status, "green")

    print("", file=stdout)
    print("Sync summary:", file=stdout)
    print(
        "  repo actions: "
        f"planned={repo_counts['planned']}, "
        f"cloned={repo_counts['cloned']}, "
        f"pulled={repo_counts['pulled']}, "
        f"errors={repo_counts['errors']}",
        file=stdout,
    )
    print(
        "  destination actions: "
        f"linked={destination_counts['linked']}, "
        f"adopted={destination_counts['adopted']}, "
        f"skipped={destination_counts['skipped']}, "
        f"conflicts={destination_counts['conflicts']}, "
        f"errors={destination_counts['errors']}, "
        f"noops={destination_counts['noops']}",
        file=stdout,
    )
    print(
        f"  final status: {_colorize(final_status, status_color, stdout)}",
        file=stdout,
    )


def _repo_summary_counts(plan: SyncPlan) -> dict[str, int]:
    return {
        "planned": sum(1 for notice in plan.notices if notice.startswith("PLAN repo ")),
        "cloned": sum(1 for notice in plan.notices if notice.startswith("CLONE repo ")),
        "pulled": sum(1 for notice in plan.notices if notice.startswith("PULL repo ")),
        "errors": sum(1 for error in plan.errors if _is_repo_error(error))
        + len(plan.duplicate_skills),
    }


def _destination_summary_counts(plan: SyncPlan) -> dict[str, int]:
    errors = sum(1 for error in plan.errors if _is_destination_error(error))
    return {
        "linked": len(plan.links),
        "adopted": len(plan.adoptions),
        "skipped": len(plan.skips),
        "conflicts": len(plan.conflicts),
        "errors": errors,
        "noops": 1 if not plan.actions and errors == 0 else 0,
    }


def _is_repo_error(error: str) -> bool:
    return (
        error.startswith("repo ")
        or error.startswith("repo URL ")
        or error.startswith("unknown repo selector")
        or error.startswith("ambiguous repo selector")
        or error.startswith("repo index ")
        or error.startswith("selector 'all'")
    )


def _is_destination_error(error: str) -> bool:
    return (
        error.startswith("destination ")
        or error.startswith("selected destinations ")
        or error.startswith("unknown destination")
        or error == "no destinations selected"
    )


def _colorize_notice(text: str, stdout: TextIO) -> str:
    if text.startswith(("CLONE repo ", "PULL repo ")):
        return _colorize(text, "green", stdout)
    if text.startswith("PLAN repo "):
        return _colorize(text, "blue", stdout)
    return text


def _colorize(text: str, color: str, stdout: TextIO | None) -> str:
    if stdout is None or not _should_color(stdout):
        return text
    codes = {
        "green": "32",
        "yellow": "33",
        "blue": "34",
        "red": "31",
    }
    code = codes.get(color)
    if code is None:
        return text
    return f"\033[{code}m{text}\033[0m"


def _should_color(stdout: TextIO) -> bool:
    force = os.environ.get("FORCE_COLOR")
    if force and force != "0":
        return True
    if "NO_COLOR" in os.environ:
        return False
    isatty = getattr(stdout, "isatty", None)
    return bool(isatty and isatty())
