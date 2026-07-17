"""Configured skill-source lanes and their persistent active selections."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from .config import Config, ConfigError


def state_path(config: Config) -> Path:
    """Return the sidecar state file; config remains the lane-definition source."""
    return config.path.parent / "lanes-state.json"


def load_active_lanes(config: Config) -> dict[str, str]:
    active = {group.name: group.default for group in config.lanes}
    path = state_path(config)
    if not path.exists():
        return active
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read lane state {path}: {exc}") from exc
    saved = raw.get("active", {})
    if not isinstance(saved, dict):
        raise ConfigError(f"lane state {path} has invalid active selections")
    for group in config.lanes:
        lane = saved.get(group.name)
        if lane is not None:
            if not isinstance(lane, str) or lane not in group.variants:
                raise ConfigError(
                    f"lane state {path} selects unknown lane {lane!r} for {group.name}"
                )
            active[group.name] = lane
    return active


def effective_config(config: Config, overrides: Mapping[str, str] | None = None) -> Config:
    """Disable inactive lane bridges while retaining all ordinary bridges."""
    active = load_active_lanes(config)
    if overrides:
        for group in config.lanes:
            requested = overrides.get(group.name)
            if requested is not None:
                if requested not in group.variants:
                    raise ConfigError(f"unknown lane {requested!r} for group {group.name}")
                active[group.name] = requested

    lane_bridges = {
        bridge_name
        for group in config.lanes
        for bridge_name in group.variants.values()
    }
    selected = {
        group.variants[active[group.name]]
        for group in config.lanes
    }
    bridges = tuple(
        replace(bridge, enabled=bridge.enabled and (bridge.name not in lane_bridges or bridge.name in selected))
        for bridge in config.bridges
    )
    return replace(config, bridges=bridges)


def resolve_switch(config: Config, terms: tuple[str, ...]) -> tuple[str, str]:
    """Resolve `switch beta` or `switch policies beta` to a group/lane pair."""
    if len(terms) == 1:
        if len(config.lanes) != 1:
            raise ConfigError("switch <lane> requires exactly one configured lane group; use switch <group> <lane>")
        group = config.lanes[0]
        lane = terms[0]
    elif len(terms) == 2:
        group_name, lane = terms
        matches = [candidate for candidate in config.lanes if candidate.name == group_name]
        if not matches:
            raise ConfigError(f"unknown lane group: {group_name}")
        group = matches[0]
    else:
        raise ConfigError("switch requires <lane> or <group> <lane>")
    if lane not in group.variants:
        raise ConfigError(f"unknown lane {lane!r} for group {group.name}")
    return group.name, lane


def save_active_lane(config: Config, group: str, lane: str) -> None:
    active = load_active_lanes(config)
    active[group] = lane
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "active": active}, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
