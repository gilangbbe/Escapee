"""Tests for fixed-world ({"world": ...}) format compatibility adapter."""

from __future__ import annotations

import json
import os

from app.game.orbital_decay_setting import ORBITAL_DECAY_SETTING
from app.schemas.fixed_world import load_setting_compat


FIXED_WORLD_PATH = os.path.join(
    os.path.dirname(__file__), "..", "app", "game", "world_010.json"
)


def test_fixed_world_envelope_converts_to_runtime_game_setting():
    with open(os.path.abspath(FIXED_WORLD_PATH), encoding="utf-8") as fh:
        data = json.load(fh)
    raw_world = data["world"]

    gs = load_setting_compat(data)

    expected_rooms = [room["id"] for room in raw_world["rooms"]]
    assert gs.rooms == expected_rooms
    assert gs.start_room == expected_rooms[0]
    assert gs.win_condition.object_id == raw_world["win_condition"]["object_id"]

    # Door/gate connections are inferred from room adjacency where the story has
    # a door-like candidate object in that room.
    for room in raw_world["rooms"]:
        from_room = room["id"]
        adjacency_targets = set(room.get("adjacency", {}).values())
        if not adjacency_targets:
            continue

        has_door_candidate = any(
            o.get("location") == from_room
            and not o.get("takeable")
            and (
                "door" in f"{o.get('id', '')} {o.get('description', '')}".lower()
                or "gate" in f"{o.get('id', '')} {o.get('description', '')}".lower()
                or o.get("state") in {"locked", "locked_bolt", "locked_room", "unlocked", "open"}
            )
            for o in raw_world["objects"]
        )
        if not has_door_candidate:
            continue

        inferred_targets = {
            obj.connects_to
            for obj in gs.objects
            if obj.location == from_room and obj.connects_to is not None
        }
        assert inferred_targets, f"expected at least one inferred connection from {from_room}"
        assert inferred_targets.issubset(adjacency_targets), (
            f"inferred targets {inferred_targets} from {from_room} must be within declared adjacency {adjacency_targets}"
        )

    # Numeric code normalization still applies when symbolic labels are used.
    for obj in gs.objects:
        if obj.requires_code and obj.code_digits:
            # If it's all digits, it should match declared keypad length.
            if obj.requires_code.isdigit():
                assert len(obj.requires_code) == obj.code_digits

    # Default co-op personas are injected when fixed-world has no player list.
    assert len(gs.players) == 2

    # Story-specific goal_completion types are accepted and preserved.
    room_goal_types = {
        room["goal_completion"]["type"]
        for room in raw_world["rooms"]
        if room.get("goal_completion") and room["goal_completion"].get("type")
    }
    assert "object_state" in room_goal_types
    assert room_goal_types.intersection({"known_info", "power_active"})


def test_legacy_gamesetting_still_loads_via_compat_loader():
    gs = load_setting_compat(ORBITAL_DECAY_SETTING)
    assert gs.rooms == ["cryo_bay", "command_deck", "airlock"]
    assert len(gs.players) == 2
