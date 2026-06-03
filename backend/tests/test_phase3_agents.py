"""Tests for Phase 3: context builder, channels, ReAct parsing, and PlayerAgent."""

from __future__ import annotations

import json

import pytest

from app.agents.player_agent import PlayerAgent
from app.agents.player_prompt import build_player_system_prompt, primary_objective
from app.agents.player_turn import PlayerTurn
from app.context.builder import build_state_view, render_recent_events, summarize_events
from app.context.channels import Event, EventKind, MessageLog
from app.engine.actions import ActionType
from app.engine.simulator import WorldSimulator
from app.engine.state import WorldState
from app.game.orbital_decay import ORBITAL_DECAY
from app.llm.json_repair import parse_and_validate
from app.schemas.blueprint import RoomBlueprint


@pytest.fixture
def blueprint() -> RoomBlueprint:
    return RoomBlueprint.model_validate(ORBITAL_DECAY)


@pytest.fixture
def state(blueprint: RoomBlueprint) -> WorldState:
    return WorldState.from_blueprint(blueprint)


# --------------------------------------------------------------------------- #
# State view (anti-forgetting / grounding)
# --------------------------------------------------------------------------- #
def test_state_view_only_lists_visible_entities(state: WorldState):
    view = build_state_view(state, "player_1")
    obj_ids = [eid for eid, _ in view.visible_objects]
    assert "supply_locker" in obj_ids
    assert "cryo_pod" in obj_ids
    # power_cell is in a different room AND hidden — must not appear.
    item_ids = [eid for eid, _ in view.visible_items]
    assert "power_cell" not in item_ids


def test_state_view_shows_locked_exit(state: WorldState):
    view = build_state_view(state, "player_1")
    locked = {d: s for d, s in view.exits}
    assert locked["command_hatch"] == "locked"


def test_state_view_render_contains_ids(state: WorldState):
    text = build_state_view(state, "player_1").render()
    assert "[supply_locker]" in text
    assert "OTHER PLAYERS HERE" in text


def test_state_view_reflects_inventory_changes(blueprint: RoomBlueprint):
    sim = WorldSimulator(blueprint)
    sim.step("player_1", PlayerTurnless(ActionType.UNLOCK, target_id="locker_lock", value="0451"))
    sim.step("player_1", PlayerTurnless(ActionType.TAKE, target_id="access_card"))
    view = build_state_view(sim.state, "player_1")
    inv_ids = [eid for eid, _ in view.inventory]
    assert "access_card" in inv_ids


# --------------------------------------------------------------------------- #
# Channels + recent/summary windowing
# --------------------------------------------------------------------------- #
def test_private_events_only_visible_to_audience():
    log = MessageLog()
    log.add(Event(EventKind.OBSERVATION, "player_1", "secret obs", turn=1,
                  public=False, audience_id="player_1"))
    log.add(Event(EventKind.SPEECH, "player_2", "hello team", turn=1, public=True))
    p1 = log.visible_to("player_1")
    p2 = log.visible_to("player_2")
    assert any("secret obs" in e.text for e in p1)
    assert not any("secret obs" in e.text for e in p2)
    assert any("hello team" in e.text for e in p2)


def test_recent_window_splits_older_events():
    log = MessageLog()
    for i in range(10):
        log.add(Event(EventKind.SPEECH, "player_1", f"msg{i}", turn=i, public=True))
    recent_text, older = render_recent_events(log, "player_1", recent_window=3)
    assert "msg9" in recent_text
    assert "msg0" not in recent_text
    assert len(older) == 7


def test_summary_is_bounded():
    log = MessageLog()
    events = [Event(EventKind.SPEECH, "p", f"m{i}", turn=i, public=True) for i in range(100)]
    summary = summarize_events("", events)
    assert len(summary.splitlines()) <= 30


# --------------------------------------------------------------------------- #
# ReAct turn parsing
# --------------------------------------------------------------------------- #
def test_parse_valid_player_turn():
    raw = json.dumps({
        "thought": "I should read the log.",
        "speak": "Checking the cryo pod.",
        "action": {"action": "inspect", "target_id": "captains_log"},
    })
    result = parse_and_validate(raw, PlayerTurn)
    assert result.ok
    assert result.value.action.action == ActionType.INSPECT


def test_parse_player_turn_with_prose_and_fences():
    raw = (
        "Sure, here's my move:\n```json\n"
        + json.dumps({"thought": "go", "speak": None,
                      "action": {"action": "look"}})
        + "\n```"
    )
    result = parse_and_validate(raw, PlayerTurn)
    assert result.ok
    assert result.value.action.action == ActionType.LOOK


def test_parse_invalid_turn_gives_repair():
    raw = json.dumps({"speak": "no thought or action"})
    result = parse_and_validate(raw, PlayerTurn)
    assert not result.ok
    assert result.repair_instruction is not None


# --------------------------------------------------------------------------- #
# Prompt assembly
# --------------------------------------------------------------------------- #
def test_system_prompt_pins_persona(blueprint: RoomBlueprint):
    persona = blueprint.players[0]
    prompt = build_player_system_prompt(persona)
    assert persona.name in prompt
    assert "JSON ONLY" in prompt
    assert '"action":"unlock"' in prompt


def test_primary_objective(blueprint: RoomBlueprint):
    assert "airlock" in primary_objective(blueprint).lower() or \
           "escape" in primary_objective(blueprint).lower()


# --------------------------------------------------------------------------- #
# PlayerAgent with a fake LLM (no Ollama needed)
# --------------------------------------------------------------------------- #
class FakeClient:
    """Stand-in OllamaClient that returns scripted responses."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls: list[list[dict]] = []

    async def chat(self, messages, *, json_mode=False, temperature=0.7):
        self.calls.append(messages)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_player_agent_decide_returns_valid_turn(blueprint: RoomBlueprint):
    state = WorldState.from_blueprint(blueprint)
    log = MessageLog()
    persona = blueprint.players[0]
    good = json.dumps({
        "thought": "look around",
        "speak": None,
        "action": {"action": "look"},
    })
    agent = PlayerAgent(persona=persona, client=FakeClient([good]))
    result = await agent.decide(state, log)
    assert result.ok
    assert result.value.action.action == ActionType.LOOK


@pytest.mark.asyncio
async def test_player_agent_repairs_then_succeeds(blueprint: RoomBlueprint):
    state = WorldState.from_blueprint(blueprint)
    log = MessageLog()
    persona = blueprint.players[0]
    bad = "I refuse to answer."
    good = json.dumps({
        "thought": "ok fine",
        "speak": "moving",
        "action": {"action": "look"},
    })
    client = FakeClient([bad, good])
    agent = PlayerAgent(persona=persona, client=client)
    result = await agent.decide(state, log)
    assert result.ok
    # It retried: a repair instruction was appended before the second call.
    assert len(client.calls) == 2


# Helper shim so state-view test reads naturally without importing PlayerAction here.
def PlayerTurnless(action_type, **kwargs):
    from app.engine.actions import PlayerAction
    return PlayerAction(action=action_type, **kwargs)
