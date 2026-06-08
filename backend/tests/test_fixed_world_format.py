"""Tests for fixed-world ({"world": ...}) format compatibility adapter."""

from __future__ import annotations

import json
import os

from app.engine.game_actions import GameAction, GameActionType
from app.engine.game_simulator import GameSimulator
from app.game.orbital_decay_setting import ORBITAL_DECAY_SETTING
from app.schemas.fixed_world import load_setting_compat


FIXED_WORLD_PATH = os.path.join(
    os.path.dirname(__file__), "..", "app", "game", "world_022.json"
)

# A structurally-specific world used by the wiring/progression tests below,
# which assert exact object ids and codes. Pinned independently of the generic
# FIXED_WORLD_PATH so repointing that constant cannot break these tests.
STRUCT_WORLD_PATH = os.path.join(
    os.path.dirname(__file__), "..", "app", "game", "world_022.json"
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
    # Every declared goal type must be one the adapter recognizes.
    assert room_goal_types.issubset(
        {"object_state", "known_info", "power_active", "has_item"}
    )


def test_legacy_gamesetting_still_loads_via_compat_loader():
    gs = load_setting_compat(ORBITAL_DECAY_SETTING)
    assert gs.rooms == ["cryo_bay", "command_deck", "airlock"]
    assert len(gs.players) == 2


def test_fixed_world_goal_gates_unlock_adjacency_progression():
    with open(os.path.abspath(STRUCT_WORLD_PATH), encoding="utf-8") as fh:
        data = json.load(fh)

    setting = load_setting_compat(data)
    sim = GameSimulator(setting)
    p = "player_1"

    # room_1 -> room_2 gate depends on cell_door_main becoming unlocked.
    sim.step(p, GameAction(action=GameActionType.TAKE, target_id="rusty_knife_hole"))
    obs = sim.step(
        p,
        GameAction(
            action=GameActionType.USE,
            item_id="rusty_knife_hole",
            target_id="cell_door_main",
        ),
    )
    assert obs.success
    exits = {to: is_open for _door, to, is_open in sim.state.exits_for(p)}
    assert exits.get("room_2") is True

    obs = sim.step(p, GameAction(action=GameActionType.MOVE, to_room="room_2"))
    assert obs.success
    assert sim.state.player_locations[p] == "room_2"

    # room_2 -> room_3 gate depends on corridor_safe (code 849) being unlocked.
    sim.step(p, GameAction(action=GameActionType.INSPECT, target_id="corridor_furniture"))
    obs = sim.step(
        p,
        GameAction(
            action=GameActionType.ENTER_CODE, target_id="corridor_safe", code="849"
        ),
    )
    assert obs.success
    exits = {to: is_open for _door, to, is_open in sim.state.exits_for(p)}
    assert exits.get("room_3") is True

    obs = sim.step(p, GameAction(action=GameActionType.MOVE, to_room="room_3"))
    assert obs.success
    assert sim.state.player_locations[p] == "room_3"


def test_fixed_world_room_wiring_is_deterministic():
    """Forward edges gate on the source room's goal; backward edges stay open.

    Regression guard for the old hash-seed-dependent door inference that could
    randomly point a forward exit backward (making a world unwinnable).
    """
    with open(os.path.abspath(STRUCT_WORLD_PATH), encoding="utf-8") as fh:
        data = json.load(fh)

    setting = load_setting_compat(data)
    edges = {
        (o.location, o.connects_to): o.requires_power
        for o in setting.objects
        if o.connects_to is not None
    }

    # Forward edges are locked behind the source room's object_state goal.
    assert edges[("room_1", "room_2")] == "state_cell_door_main_unlocked"
    assert edges[("room_2", "room_3")] == "state_corridor_safe_unlocked"
    assert edges[("room_3", "room_4")] == "state_blast_doors_unlocked"
    # Backward edges are open passages (no requirement).
    assert edges[("room_2", "room_1")] is None
    assert edges[("room_3", "room_2")] is None
    assert edges[("room_4", "room_3")] is None
