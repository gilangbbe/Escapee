"""Tests for the deterministic WorldSimulator (Phase 2)."""

from __future__ import annotations

import pytest

from app.engine.actions import ActionType, PlayerAction
from app.engine.simulator import WorldSimulator
from app.game.orbital_decay import ORBITAL_DECAY
from app.schemas.blueprint import RoomBlueprint


@pytest.fixture
def sim() -> WorldSimulator:
    bp = RoomBlueprint.model_validate(ORBITAL_DECAY)
    return WorldSimulator(bp)


# --------------------------------------------------------------------------- #
# Basic grounding
# --------------------------------------------------------------------------- #
def test_players_start_in_start_room(sim: WorldSimulator):
    assert sim.state.player_locations["player_1"] == "cryo_bay"
    assert sim.state.player_locations["player_2"] == "cryo_bay"


def test_look_lists_visible_entities(sim: WorldSimulator):
    obs = sim.step("player_1", PlayerAction(action=ActionType.LOOK))
    assert obs.success
    assert "Supply Locker" in obs.message
    assert "command_hatch" in obs.exits


def test_cannot_inspect_nonexistent_entity(sim: WorldSimulator):
    obs = sim.step("player_1", PlayerAction(action=ActionType.INSPECT, target_id="ghost"))
    assert not obs.success


def test_hidden_item_not_visible_until_revealed(sim: WorldSimulator):
    # power_cell is hidden at start.
    obs = sim.step("player_2", PlayerAction(action=ActionType.LOOK))
    assert "Spare Power Cell" not in obs.message


# --------------------------------------------------------------------------- #
# Locks reject wrong input (anti-hallucination of progress)
# --------------------------------------------------------------------------- #
def test_locked_exit_blocks_move(sim: WorldSimulator):
    obs = sim.step("player_1", PlayerAction(action=ActionType.MOVE, direction="command_hatch"))
    assert not obs.success
    assert "locked" in obs.message.lower()


def test_wrong_code_fails(sim: WorldSimulator):
    obs = sim.step(
        "player_1",
        PlayerAction(action=ActionType.UNLOCK, target_id="locker_lock", value="0000"),
    )
    assert not obs.success
    assert sim.state.lock_locked["locker_lock"] is True


def test_cannot_take_item_from_locked_container(sim: WorldSimulator):
    obs = sim.step("player_1", PlayerAction(action=ActionType.TAKE, target_id="access_card"))
    assert not obs.success


def test_key_lock_needs_key_in_inventory(sim: WorldSimulator):
    obs = sim.step(
        "player_1",
        PlayerAction(action=ActionType.UNLOCK, target_id="command_door_lock"),
    )
    assert not obs.success
    assert sim.state.lock_locked["command_door_lock"] is True


# --------------------------------------------------------------------------- #
# Puzzle reveal
# --------------------------------------------------------------------------- #
def test_solve_reveals_hidden_item(sim: WorldSimulator):
    obs = sim.step(
        "player_2",
        PlayerAction(action=ActionType.SOLVE, puzzle_id="reroute_power", answer="reroute auxiliary"),
    )
    assert obs.success
    assert sim.state.item_hidden["power_cell"] is False


def test_solve_wrong_answer_fails(sim: WorldSimulator):
    obs = sim.step(
        "player_2",
        PlayerAction(action=ActionType.SOLVE, puzzle_id="reroute_power", answer="nope"),
    )
    assert not obs.success
    assert sim.state.item_hidden["power_cell"] is True


# --------------------------------------------------------------------------- #
# Full cooperative playthrough -> WIN
# --------------------------------------------------------------------------- #
def test_full_playthrough_wins(sim: WorldSimulator):
    p1, p2 = "player_1", "player_2"

    def ok(player, action):
        obs = sim.step(player, action)
        assert obs.success, f"action failed: {action} -> {obs.message}"
        return obs

    # 1. Vega reads the captain's log (cryo_pod is an unlocked container).
    ok(p1, PlayerAction(action=ActionType.INSPECT, target_id="captains_log"))
    # 2. Unlock the supply locker with the code from the log.
    ok(p1, PlayerAction(action=ActionType.UNLOCK, target_id="locker_lock", value="0451"))
    # 3. Take the access card.
    ok(p1, PlayerAction(action=ActionType.TAKE, target_id="access_card"))
    # 4. Unlock the command door (key) and move through.
    ok(p1, PlayerAction(action=ActionType.UNLOCK, target_id="command_door_lock"))
    ok(p1, PlayerAction(action=ActionType.MOVE, direction="command_hatch"))
    assert sim.state.player_locations[p1] == "command_deck"

    # 5. Rourke also needs to be on the command deck. Move him too.
    #    (Door is now unlocked.)
    ok(p2, PlayerAction(action=ActionType.MOVE, direction="command_hatch"))

    # 6. Rourke reroutes power -> reveals the power cell.
    ok(p2, PlayerAction(action=ActionType.SOLVE, puzzle_id="reroute_power", answer="reroute auxiliary"))
    # 7. Take the now-visible power cell.
    ok(p2, PlayerAction(action=ActionType.TAKE, target_id="power_cell"))
    # 8. Unlock the airlock via the sequence (holding the power cell).
    ok(p2, PlayerAction(
        action=ActionType.UNLOCK,
        target_id="airlock_lock",
        sequence=["power_cell", "power_panel"],
    ))
    # 9. Move into the airlock -> WIN.
    obs = sim.step(p2, PlayerAction(action=ActionType.MOVE, direction="airlock_door"))
    assert obs.success
    assert obs.game_won is True
    assert sim.state.won is True
    assert sim.state.finished is True


def test_no_actions_after_finish(sim: WorldSimulator):
    sim.state.finished = True
    obs = sim.step("player_1", PlayerAction(action=ActionType.LOOK))
    assert not obs.success
    assert "over" in obs.message.lower()
