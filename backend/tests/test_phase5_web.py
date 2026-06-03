"""Tests for Phase 5: web serializers, GameRunner streaming, and the WebSocket."""

from __future__ import annotations

import json

import pytest

from app.agents.game_player_agent import GamePlayerAgent
from app.engine.game_simulator import GameSimulator
from app.game.orbital_decay_setting import ORBITAL_DECAY_SETTING
from app.schemas.game_setting import GameSetting
from app.web.runner import GameRunner
from app.web.serializers import (
    event_to_dict,
    result_to_dict,
    setup_message,
    state_snapshot,
)
from app.orchestrator.loop import GameResult
from app.context.channels import Event, EventKind


@pytest.fixture
def setting() -> GameSetting:
    return GameSetting.model_validate(ORBITAL_DECAY_SETTING)


# --------------------------------------------------------------------------- #
# Scripted fake client (reused pattern)
# --------------------------------------------------------------------------- #
class ScriptedClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def chat(self, messages, *, json_mode: bool = False, format_schema: dict | None = None, temperature: float = 0.7) -> str:
        if self._responses:
            return self._responses.pop(0)
        return json.dumps(
            {"thought": "idle", "speak": None, "action": {"action": "look"}}
        )


def turn_json(action: dict, *, thought: str = "t", speak: str | None = None) -> str:
    return json.dumps({"thought": thought, "speak": speak, "action": action})


def plan_json(steps: list[str]) -> str:
    return json.dumps({"thought": "plan", "plan": steps})


def _winning_scripts() -> tuple[list[str], list[str]]:
    # First response of each agent answers the up-front collaborative planning
    # debate (json_mode PlanProposal); the rest are the actual winning moves.
    p1 = [
        plan_json(["read the log", "open the locker", "use the access card"]),
        turn_json({"action": "inspect", "target_id": "captains_log"}, speak="Reading."),
        turn_json({"action": "enter_code", "target_id": "supply_locker", "code": "0451"}),
        turn_json({"action": "take", "target_id": "access_card"}),
        turn_json({"action": "use", "item_id": "access_card", "target_id": "command_door"}),
        turn_json({"action": "move", "to_room": "command_deck"}),
    ]
    p2 = [
        plan_json(["read the log", "open the locker", "use the access card"]),
        turn_json({"action": "look"}),
        turn_json({"action": "look"}),
        turn_json({"action": "look"}),
        turn_json({"action": "look"}),
        turn_json({"action": "move", "to_room": "command_deck"}),
        turn_json({"action": "enter_code", "target_id": "nav_console", "code": "reroute auxiliary"}),
        turn_json({"action": "take", "target_id": "power_cell"}),
        turn_json({"action": "use", "item_id": "power_cell", "target_id": "power_panel"},
                  speak="Power!"),
    ]
    return p1, p2


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #
def test_event_to_dict_shape():
    e = Event(kind=EventKind.SPEECH, actor_id="player_1", text="hi", turn=3)
    d = event_to_dict(e)
    assert d["type"] == "event"
    assert d["kind"] == "speech"
    assert d["actor_id"] == "player_1"
    assert d["turn"] == 3


def test_prompt_event_to_dict_shape():
    e = Event(kind=EventKind.PROMPT, actor_id="player_1", text="[SYSTEM]\nhi", turn=3)
    d = event_to_dict(e)
    assert d["type"] == "event"
    assert d["kind"] == "prompt"
    assert d["actor_id"] == "player_1"
    assert d["turn"] == 3


def test_decision_event_to_dict_shape():
    e = Event(
        kind=EventKind.DECISION,
        actor_id="player_1",
        text='{"action":{"action":"look"}}',
        turn=3,
    )
    d = event_to_dict(e)
    assert d["type"] == "event"
    assert d["kind"] == "decision"
    assert d["actor_id"] == "player_1"
    assert d["turn"] == 3


def test_planner_event_to_dict_shape():
    payload = {
        "reason": "out_of_policy",
        "chosen": "move to_room=command_deck",
        "candidates": [
            {
                "rank": 1,
                "action": "move to_room=command_deck",
                "score": 12.5,
                "rationale": "success",
            }
        ],
    }
    e = Event(
        kind=EventKind.PLANNER,
        actor_id="player_1",
        text="planner trace",
        turn=4,
        data=payload,
    )
    d = event_to_dict(e)
    assert d["type"] == "event"
    assert d["kind"] == "planner"
    assert d["actor_id"] == "player_1"
    assert d["turn"] == 4
    assert d["data"] == payload


def test_state_snapshot_is_grounded(setting: GameSetting):
    sim = GameSimulator(setting)
    snap = state_snapshot(sim.state)
    assert snap["type"] == "state"
    ids = {o["id"] for o in snap["objects"]}
    assert "supply_locker" in ids
    # JSON-serializable (enums rendered to strings).
    json.dumps(snap)
    locker = next(o for o in snap["objects"] if o["id"] == "supply_locker")
    assert locker["state"] == "locked"


def test_setup_message_lists_personas(setting: GameSetting):
    msg = setup_message(setting)
    assert msg["type"] == "setup"
    pids = {p["id"] for p in msg["players"]}
    assert pids == {"player_1", "player_2"}
    assert msg["objective"]


def test_result_to_dict_shape():
    r = GameResult(won=True, turns=9, reason="escaped")
    d = result_to_dict(r)
    assert d == {"type": "result", "won": True, "turns": 9, "reason": "escaped"}


# --------------------------------------------------------------------------- #
# GameRunner streaming
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_runner_streams_setup_state_events_and_result(setting: GameSetting):
    p1, p2 = _winning_scripts()
    agents = [
        GamePlayerAgent(persona=setting.players[0], client=ScriptedClient(p1)),
        GamePlayerAgent(persona=setting.players[1], client=ScriptedClient(p2)),
    ]
    runner = GameRunner(setting, agents, max_rounds=12)

    messages: list[dict] = []

    async def send(m: dict) -> None:
        messages.append(m)

    result = await runner.run(send)

    types = [m["type"] for m in messages]
    assert types[0] == "setup"
    assert "state" in types
    assert "event" in types
    assert any(m.get("kind") == "prompt" for m in messages if m["type"] == "event")
    assert any(m.get("kind") == "decision" for m in messages if m["type"] == "event")
    assert types[-1] == "result"
    assert result.won is True
    assert messages[-1]["won"] is True
    # A state snapshot follows every event.
    assert any(m["type"] == "state" and m["won"] for m in messages)


# --------------------------------------------------------------------------- #
# WebSocket endpoint (scripted agents injected via monkeypatch)
# --------------------------------------------------------------------------- #
def test_websocket_streams_a_full_game(monkeypatch, setting: GameSetting):
    from fastapi.testclient import TestClient

    import app.web.server as server

    p1, p2 = _winning_scripts()

    def fake_build_agents(s, **kwargs):
        return [
            GamePlayerAgent(persona=s.players[0], client=ScriptedClient(p1)),
            GamePlayerAgent(persona=s.players[1], client=ScriptedClient(p2)),
        ]

    monkeypatch.setattr(server, "build_agents", fake_build_agents)
    monkeypatch.setattr(server, "current_setting", lambda: setting)

    client = TestClient(server.app)
    received: list[dict] = []
    with client.websocket_connect("/ws/game?rounds=12") as ws:
        while True:
            data = ws.receive_text()
            msg = json.loads(data)
            received.append(msg)
            if msg["type"] == "result":
                break

    assert received[0]["type"] == "setup"
    final = received[-1]
    assert final["type"] == "result"
    assert final["won"] is True


def test_get_setting_endpoint(setting: GameSetting):
    from fastapi.testclient import TestClient

    import app.web.server as server

    client = TestClient(server.app)
    resp = client.get("/api/setting")
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "setup"
    assert len(body["players"]) == 2
