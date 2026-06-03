"""
JSON serializers for the web layer.

The React UI is a **spectator/observer**: it watches the whole game unfold. So
these serializers expose the full timeline and a grounded world snapshot derived
ONLY from authoritative `GameState` (never from model claims).

Three message shapes are sent over the WebSocket, each tagged by `type`:
        - "event"    : one timeline entry (speech / observation / system / prompt / decision)
  - "state"    : a full grounded world snapshot (sent after each turn)
  - "result"   : the final `GameResult` once the game ends
Plus a one-time "setup" message describing the scenario + personas at start.
"""

from __future__ import annotations

from app.context.channels import Event
from app.engine.game_state import GameState
from app.orchestrator.loop import GameResult
from app.schemas.game_setting import GameSetting, ObjectState


def event_to_dict(event: Event) -> dict:
    """Serialize a timeline Event for the observer UI."""
    return {
        "type": "event",
        "kind": event.kind.value,
        "actor_id": event.actor_id,
        "text": event.text,
        "turn": event.turn,
        "public": event.public,
        "audience_id": event.audience_id,
    }


def state_snapshot(state: GameState) -> dict:
    """A grounded, full-world snapshot for the observer state panel."""
    objects = []
    for obj in state.setting.objects:
        objects.append(
            {
                "id": obj.id,
                "location": state.object_location.get(obj.id, obj.location),
                "room": state.room_of(obj.id),
                "state": _state_value(state.object_state.get(obj.id, obj.state)),
                "takeable": obj.takeable,
                "description": obj.description,
            }
        )

    players = []
    for pid, room in state.player_locations.items():
        players.append(
            {
                "id": pid,
                "name": _player_name(state, pid),
                "room": room,
                "inventory": list(state.player_inventories.get(pid, [])),
            }
        )

    return {
        "type": "state",
        "turn": state.turn,
        "rooms": list(state.setting.rooms),
        "accessible_rooms": sorted(state.accessible_rooms),
        "objects": objects,
        "players": players,
        "power_flags": sorted(state.power_flags),
        "finished": state.finished,
        "won": state.won,
        "win_condition": {
            "object_id": state.setting.win_condition.object_id,
            "state": _state_value(state.setting.win_condition.state),
        },
    }


def setup_message(setting: GameSetting) -> dict:
    """One-time message describing the scenario + personas before play starts."""
    return {
        "type": "setup",
        "scenario": setting.scenario,
        "objective": setting.objective,
        "rooms": list(setting.rooms),
        "rules": list(setting.rules),
        "players": [
            {
                "id": p.id,
                "name": p.name,
                "role": p.role,
                "skills": list(p.skills),
                "backstory": p.backstory,
            }
            for p in setting.players
        ],
    }


def result_to_dict(result: GameResult) -> dict:
    """Serialize the final game outcome."""
    return {
        "type": "result",
        "won": result.won,
        "turns": result.turns,
        "reason": result.reason,
    }


def _state_value(state) -> str:
    return state.value if isinstance(state, ObjectState) else str(state)


def _player_name(state: GameState, player_id: str) -> str:
    for p in state.setting.players:
        if p.id == player_id:
            return p.name
    return player_id
