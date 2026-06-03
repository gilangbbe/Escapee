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

    def _infer_door_connections(self, objects: list[dict]) -> None:
        """Fill `connects_to` from room adjacency when omitted.

        The fixed format stores adjacency under rooms, while the runtime engine
        navigates through door objects with `connects_to`. We infer this mapping
        conservatively for door-like objects in each room.
        """
        by_room: dict[str, list[dict]] = {room.id: [] for room in self.rooms}
        for obj in objects:
            loc = obj.get("location")
            if loc in by_room:
                by_room[loc].append(obj)

        for room in self.rooms:
            targets = list(dict.fromkeys(room.adjacency.values()))
            if not targets:
                continue

            candidates = [
                obj
                for obj in by_room.get(room.id, [])
                if not obj.get("takeable")
                and not obj.get("connects_to")
                and ("door" in obj.get("id", "") or obj.get("state") in {"locked", "locked_bolt", "locked_room", "unlocked", "open"})
            ]
            candidates.sort(
                key=lambda obj: (
                    0
                    if "door" in f"{obj.get('id', '')} {obj.get('description', '')}".lower()
                    or "gate" in f"{obj.get('id', '')} {obj.get('description', '')}".lower()
                    else 1,
                    obj.get("id", ""),
                )
            )

            def score(candidate: dict, to_room: str) -> int:
                """Heuristically score how well a door matches a destination room."""
                cand_text = f"{candidate.get('id', '')} {candidate.get('description', '')}".lower()
                room_text = next((r.description for r in self.rooms if r.id == to_room), "").lower()
                room_id = to_room.replace("_", " ").lower()
                cand_tokens = set(re.findall(r"[a-z0-9]+", cand_text))
                room_tokens = set(re.findall(r"[a-z0-9]+", room_text + " " + room_id))
                overlap = len(cand_tokens & room_tokens)

                # Prefer explicit semantic hints in the door description.
                if to_room in cand_text:
                    overlap += 4
                if "path to" in cand_text and to_room.replace("_", " ") in cand_text:
                    overlap += 4
                return overlap

            remaining = set(targets)
            for candidate in candidates:
                if not remaining:
                    break
                best_room = max(remaining, key=lambda to_room: score(candidate, to_room))
                # If every option is equally bad, still pick the best remaining room
                # rather than leaving the door disconnected.
                candidate["connects_to"] = best_room
                remaining.discard(best_room)

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
        self._infer_door_connections(objects)

        # Default cooperative cast when personas are not authored in fixed format.
        default_players = [
            PlayerPersona(
                id="player_1",
                name="Alex Quinn",
                role="Field Analyst",
                skills=["observe", "reason", "decode"],
                backstory="A methodical investigator who tracks clues under pressure.",
            ).model_dump(),
            PlayerPersona(
                id="player_2",
                name="Riley Sato",
                role="Systems Operator",
                skills=["repair", "override", "route_power"],
                backstory="A pragmatic engineer who can restore failing systems quickly.",
            ).model_dump(),
        ]

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
                "players": default_players,
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
