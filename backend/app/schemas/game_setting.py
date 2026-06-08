"""
GameSetting schema (v2)
=======================

This is the NEW escape-room schema that the GM LLM produces. It replaces the
Phase 1 `RoomBlueprint` "rooms/objects/items/locks/puzzles" model with a flatter,
more expressive **object + state + requirement** model.

Design highlights:
- Everything in the world is a `GameObject`. Takeable objects act as items.
- An object's `location` may be a ROOM id OR another OBJECT id (nesting /
  containment). E.g. a key inside a safe: key.location == "<safe_id>".
- Each object carries optional REQUIREMENT fields that gate interaction:
    requires_code   -> opened by entering a code
    requires_tool   -> opened by using a specific (takeable) object on it
    requires_liquid -> opened by using an object whose liquid_property matches
    requires_power  -> auto-opens when a named power flag is active
    fuses           -> a power source; flipping a fuse ON activates a power flag
- Win is declared as a target object reaching a target state.

Anti-hallucination at PARSE time:
The `_validate_referential_integrity` model validator guarantees every id
reference resolves (locations, tools, reveals, connections, win target, player
clues) and that there are no containment cycles. A generated world that points at
nonexistent entities is rejected before it can ever reach the engine.

Fidelity note:
The faithful fields (requires_*, fuses, slot_description, note, contains_info,
code_digits) mirror exactly what the GM LLM emits. A few OPTIONAL fields
(reveals, liquid_property, connects_to, provides_power, plus the players /
clues / start_room extensions) are engine-resolution hints; they default to
None/empty so a raw generated world still validates.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ObjectState(str, Enum):
    """Lifecycle states an object can hold (initial + runtime + target)."""

    # Initial states (as emitted by the GM)
    FIXED = "fixed"              # static scenery, not takeable
    VISIBLE = "visible"          # present and interactable
    HIDDEN = "hidden"            # not visible until revealed
    LOCKED = "locked"            # sealed; needs code/liquid/power
    LOCKED_BOLT = "locked_bolt"  # bolted shut; needs a tool
    LOCKED_ROOM = "locked_room"  # lives in a room not yet accessible

    # Runtime / target states
    UNLOCKED = "unlocked"
    OPEN = "open"
    TAKEN = "taken"
    POWERED = "powered"


# States that mean "this container/door is passable / its contents are reachable".
OPEN_STATES = {ObjectState.VISIBLE, ObjectState.UNLOCKED, ObjectState.OPEN,
               ObjectState.POWERED, ObjectState.FIXED}


class GameObject(BaseModel):
    """A single world object. Takeable objects double as inventory items."""

    id: str = Field(..., description="Unique snake_case id.")
    location: str = Field(
        ..., description="Room id OR another object id (containment)."
    )
    description: str
    state: ObjectState

    interactable: bool = Field(True, description="Can players act on it?")
    takeable: bool = Field(False, description="Can it be picked up?")

    # ---- Faithful requirement fields (emitted by the GM LLM) ----
    requires_code: Optional[str] = Field(
        None, description="Code that opens this object (string)."
    )
    code_digits: Optional[int] = Field(None, description="Expected code length.")
    requires_tool: Optional[str] = Field(
        None, description="Object id that must be USED on this to open it."
    )
    requires_power: Optional[str] = Field(
        None, description="Power-flag name that auto-opens this when active."
    )
    requires_liquid: Optional[str] = Field(
        None, description="liquid_property value required to open this."
    )
    fuses: Optional[dict[str, str]] = Field(
        None, description="Power source: {fuse_letter: 'ON'|'OFF'}."
    )
    slot_description: Optional[str] = None
    note: Optional[str] = None
    contains_info: Optional[str] = Field(
        None, description="Machine tag for info learned by inspecting this."
    )

    # ---- Optional engine-resolution hints (default None; keep raw worlds valid) ----
    reveals: Optional[str] = Field(
        None, description="Object id revealed (HIDDEN->VISIBLE) when this opens."
    )
    liquid_property: Optional[str] = Field(
        None, description="If this is a liquid, its property, e.g. 'pH_7'."
    )
    connects_to: Optional[str] = Field(
        None, description="If this is a door, the room id it leads to."
    )
    provides_power: Optional[str] = Field(
        None, description="Power-flag this object activates once satisfied."
    )


class WinCondition(BaseModel):
    """The team wins when `object_id` reaches `state`."""

    object_id: str
    state: ObjectState


class PlayerPersona(BaseModel):
    """A cooperating player (multi-agent extension over the raw world).

    Besides flavor (name/role/skills/backstory), a persona may declare its own
    LLM binding so a team can mix models — e.g. one player on ``qwen2.5:7b`` and
    another on ``llama3.1:8b``. ``model``/``temperature`` are optional overrides;
    when omitted the runner's defaults apply. ``personality`` is a short trait
    line injected into the system prompt to differentiate how each agent behaves.
    """

    id: str
    name: str
    role: str
    skills: list[str] = Field(default_factory=list)
    backstory: str = ""
    personality: str = Field(
        "", description="Short behavioral trait line for prompt flavor."
    )
    model: Optional[str] = Field(
        None, description="Per-persona LLM model id; falls back to the runner default."
    )
    temperature: Optional[float] = Field(
        None, description="Per-persona sampling temperature; falls back to the default."
    )


class PlayerClue(BaseModel):
    """Asymmetric private knowledge for one player."""

    player_id: str
    clue: str


class GameSetting(BaseModel):
    """A complete, validated escape-room world produced by the GM."""

    scenario: str = Field(..., description="Narrative setting / atmosphere.")
    objective: str = Field(..., description="The team's goal in one sentence.")
    rooms: list[str] = Field(..., min_length=1, description="Room ids.")
    objects: list[GameObject] = Field(..., min_length=1)
    rules: list[str] = Field(default_factory=list)
    win_condition: WinCondition
    solution_path: list[str] = Field(default_factory=list)

    # Multi-agent extensions (optional; raw generated worlds may omit these).
    start_room: Optional[str] = Field(
        None, description="Room players start in (defaults to first room)."
    )
    players: list[PlayerPersona] = Field(default_factory=list)
    player_clues: list[PlayerClue] = Field(default_factory=list)

    # ------------------------------------------------------------------ #
    # Referential integrity — anti-hallucination at parse time
    # ------------------------------------------------------------------ #
    @model_validator(mode="after")
    def _validate_referential_integrity(self) -> "GameSetting":
        room_ids = set(self.rooms)
        objects_by_id = {o.id: o for o in self.objects}
        object_ids = set(objects_by_id)
        player_ids = {p.id for p in self.players}

        errors: list[str] = []

        def require(cond: bool, msg: str) -> None:
            if not cond:
                errors.append(msg)

        if len(object_ids) != len(self.objects):
            errors.append("Duplicate object ids detected.")
        if len(room_ids) != len(self.rooms):
            errors.append("Duplicate room ids detected.")

        if self.start_room is not None:
            require(self.start_room in room_ids,
                    f"start_room '{self.start_room}' is not a defined room.")

        for obj in self.objects:
            # location resolves to a room or another object
            require(
                obj.location in room_ids or obj.location in object_ids,
                f"Object '{obj.id}' location '{obj.location}' is neither room nor object.",
            )
            require(obj.location != obj.id, f"Object '{obj.id}' cannot contain itself.")

            if obj.requires_tool is not None:
                require(obj.requires_tool in object_ids,
                        f"Object '{obj.id}' requires_tool '{obj.requires_tool}' is not an object.")
            if obj.reveals is not None:
                require(obj.reveals in object_ids,
                        f"Object '{obj.id}' reveals '{obj.reveals}' is not an object.")
            if obj.connects_to is not None:
                require(obj.connects_to in room_ids,
                        f"Object '{obj.id}' connects_to '{obj.connects_to}' is not a room.")
            if obj.fuses is not None:
                for k, v in obj.fuses.items():
                    require(v in ("ON", "OFF"),
                            f"Object '{obj.id}' fuse '{k}' must be 'ON' or 'OFF'.")

        # Containment cycle detection (follow location chains through objects).
        for obj in self.objects:
            seen: set[str] = set()
            cur = obj
            while cur.location in objects_by_id:
                if cur.id in seen:
                    errors.append(f"Containment cycle involving '{obj.id}'.")
                    break
                seen.add(cur.id)
                cur = objects_by_id[cur.location]

        require(self.win_condition.object_id in object_ids,
                f"win_condition.object_id '{self.win_condition.object_id}' is not an object.")

        for clue in self.player_clues:
            require(clue.player_id in player_ids,
                    f"player_clue references unknown player '{clue.player_id}'.")

        if errors:
            raise ValueError("GameSetting integrity errors:\n - " + "\n - ".join(errors))
        return self

    # ------------------------------------------------------------------ #
    # Convenience
    # ------------------------------------------------------------------ #
    def object(self, object_id: str) -> Optional[GameObject]:
        for o in self.objects:
            if o.id == object_id:
                return o
        return None

    def effective_start_room(self) -> str:
        return self.start_room or self.rooms[0]
