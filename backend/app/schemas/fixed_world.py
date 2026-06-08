"""Compatibility schema + adapter for the fixed story format ({"world": ...}).

The simulator runtime uses `GameSetting`. This module lets authored story files
use the new stable format while preserving the existing deterministic engine by
converting fixed-world payloads into `GameSetting`.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.game_setting import GameSetting, ObjectState, PlayerPersona


class GoalCompletion(BaseModel):
    """Story-authored room completion condition.

    This is intentionally flexible: different stories can use different
    completion types (power_active, has_item, known_info, object_state, etc.).
    The adapter only needs to preserve the data, not interpret every variant.
    """

    model_config = ConfigDict(extra="allow")

    type: str
    info: str | None = None
    object_id: str | None = None
    state: str | None = None


class FixedRoom(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    description: str = ""
    adjacency: dict[str, str] = Field(default_factory=dict)
    goal: str = ""
    goal_completion: GoalCompletion | None = None
    key_objects: list[str] = Field(default_factory=list)


class FixedObject(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    location: str
    description: str
    state: ObjectState

    interactable: bool = True
    takeable: bool = False

    requires_code: str | None = None
    code_digits: int | None = None
    requires_tool: str | None = None
    requires_power: str | None = None
    requires_liquid: str | None = None
    fuses: dict[str, str] | None = None
    slot_description: str | None = None
    note: str | None = None
    contains_info: str | None = None

    reveals: str | None = None
    liquid_property: str | None = None
    connects_to: str | None = None
    provides_power: str | None = None


class FixedWinCondition(BaseModel):
    model_config = ConfigDict(extra="allow")

    object_id: str
    state: ObjectState


class FixedWorld(BaseModel):
    model_config = ConfigDict(extra="allow")

    scenario: str
    objective: str
    rooms: list[FixedRoom]
    objects: list[FixedObject]
    rules: list[str] = Field(default_factory=list)
    solution_path: list[str] = Field(default_factory=list)
    win_condition: FixedWinCondition

    @model_validator(mode="after")
    def _validate_rooms(self) -> "FixedWorld":
        ids = [room.id for room in self.rooms]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate room ids in fixed-world format.")
        return self

    @staticmethod
    def _goal_condition_flag(goal: GoalCompletion | None) -> str | None:
        """Map a room goal_completion into a deterministic condition flag."""
        if goal is None:
            return None
        gtype = (goal.type or "").strip().lower()
        payload = goal.model_dump(exclude_none=True)

        if gtype == "power_active":
            power_id = payload.get("id") or payload.get("object_id") or goal.info
            return str(power_id) if power_id else None
        if gtype == "has_item" and goal.object_id:
            return f"has_item_{goal.object_id}"
        if gtype == "known_info" and goal.info:
            return f"known_info_{goal.info}"
        if gtype == "object_state" and goal.object_id and goal.state:
            return f"state_{goal.object_id}_{goal.state}"
        return None

    def _wire_room_progression(self, objects: list[dict]) -> None:
        """Deterministically wire room-to-room movement from adjacency + goals.

        This replaces the old fuzzy "door inference" heuristic, which was
        non-deterministic (it iterated a ``set`` and broke ties by hash order, so
        a forward door could randomly point backward on a different process) and
        frequently mis-assigned scenery / decoy objects as the room exit.

        Model (deterministic, order-aware):
          * Rooms are ordered by their position in the ``rooms`` list.
          * For every adjacency edge ``A -> B``:
              - FORWARD  (index(B) > index(A)): a gate locked behind room A's
                ``goal_completion`` condition. Solving room A's puzzle flips the
                derived condition flag and the gate auto-opens. If room A has no
                goal, the forward gate is an open passage.
              - BACKWARD (index(B) < index(A)): always an OPEN passage so players
                may freely backtrack to rooms they have already cleared.
          * Author-supplied ``connects_to`` edges are respected and never
            duplicated.

        Each gate is a non-interactable connector object; it appears to players
        only as an EXIT (open/locked), never as an inspectable object.
        """
        room_index = {room.id: i for i, room in enumerate(self.rooms)}

        explicit_pairs = {
            (obj.get("location"), obj.get("connects_to"))
            for obj in objects
            if obj.get("connects_to")
        }
        existing_ids = {str(obj.get("id")) for obj in objects if obj.get("id")}

        for room in self.rooms:
            src_idx = room_index[room.id]
            condition_flag = self._goal_condition_flag(room.goal_completion)

            for to_room in dict.fromkeys(room.adjacency.values()):
                if to_room not in room_index:
                    continue
                if (room.id, to_room) in explicit_pairs:
                    continue

                forward = room_index[to_room] > src_idx
                gate_condition = condition_flag if forward else None
                gate_state = "locked" if gate_condition else "unlocked"

                gate_id = f"gate_{room.id}_to_{to_room}"
                suffix = 1
                while gate_id in existing_ids:
                    suffix += 1
                    gate_id = f"gate_{room.id}_to_{to_room}_{suffix}"

                gate: dict = {
                    "id": gate_id,
                    "location": room.id,
                    "description": f"A passage from {room.id} to {to_room}.",
                    "state": gate_state,
                    "interactable": False,
                    "takeable": False,
                    "connects_to": to_room,
                }
                if gate_condition:
                    gate["requires_power"] = gate_condition
                objects.append(gate)
                existing_ids.add(gate_id)

    @staticmethod
    def _normalize_code(obj: dict) -> None:
        """Normalize symbolic codes like `door_code_842` -> `842` when numeric."""
        code = obj.get("requires_code")
        digits = obj.get("code_digits")
        if not isinstance(code, str) or not digits:
            return
        numbers = re.findall(r"[0-9]{1,8}", code)
        if len(numbers) == 1 and len(numbers[0]) == int(digits):
            obj["requires_code"] = numbers[0]

    def to_game_setting(self) -> GameSetting:
        room_ids = [room.id for room in self.rooms]
        objects = [obj.model_dump(exclude_none=True) for obj in self.objects]

        for obj in objects:
            self._normalize_code(obj)
        self._wire_room_progression(objects)

        # Personas: respect any cast authored in the fixed-world payload (the
        # `players` field is allowed via extra="allow"); otherwise deal a default
        # cooperative roster from the editable catalog. Defined in one place so
        # the cast — and per-player LLM bindings — stay modular.
        from app.game.personas import build_roster

        authored = getattr(self, "players", None)
        if authored:
            players = [PlayerPersona.model_validate(p).model_dump() for p in authored]
        else:
            players = [p.model_dump() for p in build_roster(count=2)]

        return GameSetting.model_validate(
            {
                "scenario": self.scenario,
                "objective": self.objective,
                "rooms": room_ids,
                "start_room": room_ids[0] if room_ids else None,
                "objects": objects,
                "rules": self.rules,
                "solution_path": self.solution_path,
                "win_condition": self.win_condition.model_dump(),
                "players": players,
                "player_clues": [],
            }
        )


class FixedWorldEnvelope(BaseModel):
    world: FixedWorld


def load_setting_compat(data: dict) -> GameSetting:
    """Load either legacy GameSetting dicts or fixed-world envelope dicts."""
    if "world" in data:
        envelope = FixedWorldEnvelope.model_validate(data)
        return envelope.world.to_game_setting()
    return GameSetting.model_validate(data)
