"""Tests for the deterministic GameSimulator (v2 schema)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.engine.game_actions import GameAction, GameActionType
from app.engine.game_simulator import GameSimulator
from app.game.orbital_decay_setting import ORBITAL_DECAY_SETTING
from app.schemas.game_setting import GameSetting, ObjectState


@pytest.fixture
def sim() -> GameSimulator:
    return GameSimulator(GameSetting.model_validate(ORBITAL_DECAY_SETTING))


A = GameActionType


def test_players_start_in_start_room(sim: GameSimulator):
    assert sim.state.player_locations["player_1"] == "cryo_bay"
    assert sim.state.player_locations["player_2"] == "cryo_bay"


def test_look_lists_visible_objects(sim: GameSimulator):
    obs = sim.step("player_1", GameAction(action=A.LOOK))
    assert obs.success
    assert "supply_locker" in obs.message
    # command_deck objects are not visible from cryo_bay.
    assert "nav_console" not in obs.message


def test_hidden_object_not_visible(sim: GameSimulator):
    # power_cell is hidden until nav_console is rerouted.
    assert not sim.state.is_visible_to("power_cell", "player_2")


def test_cannot_take_from_locked_container(sim: GameSimulator):
    obs = sim.step("player_1", GameAction(action=A.TAKE, target_id="access_card"))
    assert not obs.success


def test_wrong_code_fails(sim: GameSimulator):
    obs = sim.step(
        "player_1",
        GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0000"),
    )
    assert not obs.success
    assert sim.state.object_state["supply_locker"] == ObjectState.LOCKED


def test_correct_code_opens_container(sim: GameSimulator):
    obs = sim.step(
        "player_1",
        GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"),
    )
    assert obs.success
    # Now the access card is reachable.
    assert sim.state.is_visible_to("access_card", "player_1")


def test_use_wrong_item_does_nothing(sim: GameSimulator):
    sim.step("player_1", GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"))
    sim.step("player_1", GameAction(action=A.TAKE, target_id="access_card"))
    # Using the access card on the power panel should do nothing useful.
    obs = sim.step("player_1", GameAction(action=A.USE, item_id="access_card", target_id="command_door"))
    assert obs.success  # command_door requires access_card -> this actually opens it
    assert sim.state.object_state["command_door"] == ObjectState.UNLOCKED


def test_move_blocked_until_door_open(sim: GameSimulator):
    obs = sim.step("player_1", GameAction(action=A.MOVE, to_room="command_deck"))
    assert not obs.success


def test_full_cooperative_playthrough_wins(sim: GameSimulator):
    p1, p2 = "player_1", "player_2"

    def ok(player, action):
        obs = sim.step(player, action)
        assert obs.success, f"failed: {action} -> {obs.message}"
        return obs

    # Vega reads the log, opens the locker, takes the card, opens the door.
    ok(p1, GameAction(action=A.INSPECT, target_id="captains_log"))
    ok(p1, GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"))
    ok(p1, GameAction(action=A.TAKE, target_id="access_card"))
    ok(p1, GameAction(action=A.USE, item_id="access_card", target_id="command_door"))
    assert sim.state.object_state["command_door"] == ObjectState.UNLOCKED

    # Both move to the command deck.
    ok(p1, GameAction(action=A.MOVE, to_room="command_deck"))
    ok(p2, GameAction(action=A.MOVE, to_room="command_deck"))

    # Rourke reroutes power, takes the cell, slots it -> airlock unlocks -> WIN.
    ok(p2, GameAction(action=A.ENTER_CODE, target_id="nav_console", code="reroute auxiliary"))
    assert sim.state.is_visible_to("power_cell", "player_2")
    ok(p2, GameAction(action=A.TAKE, target_id="power_cell"))
    obs = sim.step(p2, GameAction(action=A.USE, item_id="power_cell", target_id="power_panel"))
    assert obs.success
    assert obs.game_won is True
    assert sim.state.object_state["airlock_door"] == ObjectState.UNLOCKED
    assert sim.state.won is True


def test_no_actions_after_finish(sim: GameSimulator):
    sim.state.finished = True
    obs = sim.step("player_1", GameAction(action=A.LOOK))
    assert not obs.success
    assert "over" in obs.message.lower()


def test_open_state_satisfies_unlocked_win_condition(sim: GameSimulator):
    """Regression: OPEN should satisfy a win target declared as UNLOCKED."""
    sim.state.object_state["airlock_door"] = ObjectState.OPEN
    obs = sim.step("player_1", GameAction(action=A.LOOK))
    assert obs.game_won is True
    assert sim.state.won is True


def test_bracketed_ids_are_normalized(sim: GameSimulator):
    """7B models often copy the [id] display form verbatim; we strip brackets."""
    a = GameAction(action=A.INSPECT, target_id="[captains_log]")
    assert a.target_id == "captains_log"

    obs = sim.step("player_1", a)
    assert obs.success
    assert "captain" in obs.message.lower()

    # Also normalizes item_id, to_room, to_player_id.
    g = GameAction(
        action=A.GIVE, item_id=" [access_card] ", to_player_id="[player_2]"
    )
    assert g.item_id == "access_card"
    assert g.to_player_id == "player_2"
    m = GameAction(action=A.MOVE, to_room="[command_deck]")
    assert m.to_room == "command_deck"


def test_give_accepts_player_display_name(sim: GameSimulator):
    # Setup: player_1 obtains the access card and both are in the same room.
    sim.step("player_1", GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"))
    sim.step("player_1", GameAction(action=A.TAKE, target_id="access_card"))

    obs = sim.step(
        "player_1",
        GameAction(action=A.GIVE, item_id="access_card", to_player_id="Kade Rourke"),
    )
    assert obs.success
    assert "access_card" not in sim.state.player_inventories["player_1"]
    assert "access_card" in sim.state.player_inventories["player_2"]


def test_use_action_misrouted_target_is_auto_corrected(sim: GameSimulator):
    """Regression: small models may put USE target in to_player_id by mistake."""
    sim.step("player_1", GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"))
    sim.step("player_1", GameAction(action=A.TAKE, target_id="access_card"))

    action = GameAction(
        action=A.USE,
        item_id="access_card",
        target_id=None,
        to_player_id="command_door",  # wrong field from model
    )
    assert action.target_id == "command_door"
    assert action.to_player_id is None

    obs = sim.step("player_1", action)
    assert obs.success
    assert sim.state.object_state["command_door"] == ObjectState.UNLOCKED


def test_use_requires_item_and_target_ids():
    with pytest.raises(ValidationError):
        GameAction(action=A.USE, item_id="access_card", target_id=None)


def test_opening_object_reveals_its_contained_info():
    """Regression: solving an object via USE/ENTER_CODE must reveal its own
    `contains_info`, just like inspecting it. Otherwise a downstream lock or
    known_info gate that depends on that info can never be satisfied (the
    world_020 failure: a console unlocked with a tool kept its code hidden).
    """
    setting = GameSetting.model_validate(
        {
            "scenario": "test",
            "objective": "Open the final door.",
            "rooms": ["room_1"],
            "start_room": "room_1",
            "objects": [
                {
                    "id": "console",
                    "location": "room_1",
                    "description": "A console.",
                    "state": "locked",
                    "interactable": True,
                    "requires_tool": "keycard",
                    "contains_info": "exit_code",
                },
                {
                    "id": "keycard",
                    "location": "room_1",
                    "description": "A keycard.",
                    "state": "visible",
                    "interactable": True,
                    "takeable": True,
                },
                {
                    "id": "final_door",
                    "location": "room_1",
                    "description": "The final door.",
                    "state": "locked",
                    "interactable": True,
                    "requires_code": "exit_code",
                },
            ],
            "win_condition": {"object_id": "final_door", "state": "unlocked"},
            "players": [{"id": "player_1", "name": "Tester", "role": "tester"}],
        }
    )
    sim = GameSimulator(setting)
    sim.step("player_1", GameAction(action=A.TAKE, target_id="keycard"))
    obs = sim.step(
        "player_1",
        GameAction(action=A.USE, item_id="keycard", target_id="console"),
    )
    assert obs.success
    # The code held inside the console is now known to the team.
    assert "exit_code" in sim.state.discovered_info
    # And it can be used to open the dependent lock.
    obs = sim.step(
        "player_1",
        GameAction(action=A.ENTER_CODE, target_id="final_door", code="exit_code"),
    )
    assert obs.success
    assert sim.state.won is True

