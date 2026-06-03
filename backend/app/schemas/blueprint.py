"""
GM Blueprint Schema
===================

Pydantic v2 models that define the *structure* of an escape room the Game Master
(GM) LLM must produce. This schema is the contract between the GM LLM and the
deterministic World Simulator (Phase 2).

Design rules that fight hallucination:
- Every reference between entities uses an explicit string `id`.
- A model-level validator (`_validate_referential_integrity`) guarantees that
  every id referenced (exits, lock targets, puzzle rewards, win conditions,
  per-player clues) actually exists in the blueprint.
- The GM cannot invent dangling items, locks, or rooms: invalid blueprints are
  rejected before they ever reach the engine.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class LockType(str, Enum):
    """How a lock is opened."""

    KEY = "key"          # opened by using a specific item id
    CODE = "code"        # opened by entering a string/number code
    SEQUENCE = "sequence"  # opened by performing actions in a specific order


class RewardType(str, Enum):
    """What solving a puzzle does to the world."""

    UNLOCK = "unlock"          # set a lock's state to unlocked
    REVEAL_ITEM = "reveal_item"  # make a hidden item visible / available
    REVEAL_OBJECT = "reveal_object"  # make a hidden object visible
    SET_FLAG = "set_flag"      # set an arbitrary world flag to true


class WinType(str, Enum):
    """How the team wins."""

    REACH_ROOM = "reach_room"      # any/all players reach a target room
    OBTAIN_ITEM = "obtain_item"    # the team holds a specific item
    SET_FLAG = "set_flag"          # a specific world flag becomes true


# --------------------------------------------------------------------------- #
# Leaf entities
# --------------------------------------------------------------------------- #
class Item(BaseModel):
    """A pickup-able / usable object that can live in inventories or containers."""

    id: str = Field(..., description="Unique snake_case id, e.g. 'brass_key'.")
    name: str = Field(..., description="Human-readable name shown to players.")
    description: str = Field(..., description="What the player sees on inspection.")
    hidden: bool = Field(
        False, description="If true, item is not visible until revealed by a puzzle."
    )


class Lock(BaseModel):
    """A barrier guarding an exit or a container."""

    id: str = Field(..., description="Unique snake_case id, e.g. 'vault_lock'.")
    lock_type: LockType
    locked: bool = Field(True, description="Initial state; True = locked.")
    # For KEY locks: the item id that opens it.
    key_item_id: Optional[str] = Field(
        None, description="Required for lock_type=key: item id that opens this lock."
    )
    # For CODE locks: the correct code. Never shown to players directly.
    code_solution: Optional[str] = Field(
        None, description="Required for lock_type=code: the secret code string."
    )
    # For SEQUENCE locks: ordered list of object ids/actions.
    sequence_solution: list[str] = Field(
        default_factory=list,
        description="Required for lock_type=sequence: the correct ordered ids.",
    )
    hint: Optional[str] = Field(
        None, description="Optional in-world hint shown when inspecting the lock."
    )

    @model_validator(mode="after")
    def _check_solution_present(self) -> "Lock":
        if self.lock_type == LockType.KEY and not self.key_item_id:
            raise ValueError(f"Lock '{self.id}': key lock requires key_item_id.")
        if self.lock_type == LockType.CODE and not self.code_solution:
            raise ValueError(f"Lock '{self.id}': code lock requires code_solution.")
        if self.lock_type == LockType.SEQUENCE and not self.sequence_solution:
            raise ValueError(
                f"Lock '{self.id}': sequence lock requires sequence_solution."
            )
        return self


class GameObject(BaseModel):
    """A fixed feature of a room (desk, painting, safe, terminal...)."""

    id: str = Field(..., description="Unique snake_case id, e.g. 'oak_desk'.")
    name: str
    description: str
    hidden: bool = Field(
        False, description="If true, not visible until revealed by a puzzle."
    )
    is_container: bool = Field(
        False, description="If true, can hold items in `contains`."
    )
    contains: list[str] = Field(
        default_factory=list,
        description="Item ids stored inside this container.",
    )
    lock_id: Optional[str] = Field(
        None,
        description="If set, the container/object is sealed by this lock until opened.",
    )


class Exit(BaseModel):
    """A directed connection from one room to another."""

    direction: str = Field(..., description="e.g. 'north', 'door', 'hatch'.")
    to_room_id: str = Field(..., description="Target room id.")
    lock_id: Optional[str] = Field(
        None, description="If set, this exit is sealed until the lock is opened."
    )


class Room(BaseModel):
    """A single location in the escape room."""

    id: str = Field(..., description="Unique snake_case id, e.g. 'lobby'.")
    name: str
    description: str = Field(..., description="Narrative description on entering.")
    objects: list[str] = Field(
        default_factory=list, description="GameObject ids present in this room."
    )
    items: list[str] = Field(
        default_factory=list,
        description="Item ids lying loose in this room (not inside a container).",
    )
    exits: list[Exit] = Field(default_factory=list)


class Puzzle(BaseModel):
    """A solvable challenge whose reward mutates the world."""

    id: str = Field(..., description="Unique snake_case id, e.g. 'cipher_puzzle'.")
    description: str = Field(..., description="What the players perceive / must do.")
    solution: str = Field(
        ..., description="Engine-checked answer (code, item id, or phrase)."
    )
    reward_type: RewardType
    reward_target_id: str = Field(
        ...,
        description="Id affected by the reward (lock id, item id, object id, or flag name).",
    )


class PlayerClue(BaseModel):
    """Asymmetric, private information given to exactly one player."""

    player_id: str = Field(..., description="Which player receives this clue.")
    clue: str = Field(..., description="Private knowledge only this player has.")


class PlayerPersona(BaseModel):
    """A player's role/skills — drives asymmetric collaboration."""

    id: str = Field(..., description="Unique player id, e.g. 'player_1'.")
    name: str = Field(..., description="Character name, e.g. 'The Engineer'.")
    role: str = Field(..., description="Short role label, e.g. 'Hacker'.")
    skills: list[str] = Field(
        default_factory=list,
        description="Special competencies that gate certain actions.",
    )
    backstory: str = Field("", description="Optional flavor for persona prompting.")


class WinCondition(BaseModel):
    """Defines how the team escapes."""

    win_type: WinType
    target_id: str = Field(
        ..., description="Room id, item id, or flag name depending on win_type."
    )
    description: str = Field("", description="Human-readable victory description.")


# --------------------------------------------------------------------------- #
# Root blueprint
# --------------------------------------------------------------------------- #
class RoomBlueprint(BaseModel):
    """The complete, validated description of an escape room produced by the GM."""

    title: str = Field(..., description="Name of the escape room scenario.")
    theme: str = Field(..., description="Setting/atmosphere, e.g. 'derelict space station'.")
    intro_narrative: str = Field(
        ..., description="Opening text read to all players at game start."
    )

    start_room_id: str = Field(..., description="Room id where all players begin.")

    rooms: list[Room] = Field(..., min_length=1)
    objects: list[GameObject] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)
    locks: list[Lock] = Field(default_factory=list)
    puzzles: list[Puzzle] = Field(default_factory=list)

    players: list[PlayerPersona] = Field(..., min_length=1)
    player_clues: list[PlayerClue] = Field(default_factory=list)

    win_conditions: list[WinCondition] = Field(..., min_length=1)

    # ----------------------------------------------------------------- #
    # Referential integrity: the heart of anti-hallucination at parse time
    # ----------------------------------------------------------------- #
    @model_validator(mode="after")
    def _validate_referential_integrity(self) -> "RoomBlueprint":
        room_ids = {r.id for r in self.rooms}
        object_ids = {o.id for o in self.objects}
        item_ids = {i.id for i in self.items}
        lock_ids = {lk.id for lk in self.locks}
        player_ids = {p.id for p in self.players}

        errors: list[str] = []

        def require(condition: bool, msg: str) -> None:
            if not condition:
                errors.append(msg)

        # Unique ids across each category
        for label, ids, models in [
            ("room", room_ids, self.rooms),
            ("object", object_ids, self.objects),
            ("item", item_ids, self.items),
            ("lock", lock_ids, self.locks),
            ("player", player_ids, self.players),
        ]:
            if len(ids) != len(models):
                errors.append(f"Duplicate {label} ids detected.")

        # start room exists
        require(self.start_room_id in room_ids,
                f"start_room_id '{self.start_room_id}' is not a defined room.")

        # Rooms reference valid objects/items/exits/locks
        for room in self.rooms:
            for oid in room.objects:
                require(oid in object_ids,
                        f"Room '{room.id}' references unknown object '{oid}'.")
            for iid in room.items:
                require(iid in item_ids,
                        f"Room '{room.id}' references unknown item '{iid}'.")
            for ex in room.exits:
                require(ex.to_room_id in room_ids,
                        f"Room '{room.id}' exit goes to unknown room '{ex.to_room_id}'.")
                if ex.lock_id is not None:
                    require(ex.lock_id in lock_ids,
                            f"Room '{room.id}' exit references unknown lock '{ex.lock_id}'.")

        # Objects: containers + locks + contained items
        for obj in self.objects:
            if obj.lock_id is not None:
                require(obj.lock_id in lock_ids,
                        f"Object '{obj.id}' references unknown lock '{obj.lock_id}'.")
            for iid in obj.contains:
                require(iid in item_ids,
                        f"Object '{obj.id}' contains unknown item '{iid}'.")

        # Locks: key locks point to real items
        for lk in self.locks:
            if lk.lock_type == LockType.KEY and lk.key_item_id is not None:
                require(lk.key_item_id in item_ids,
                        f"Lock '{lk.id}' key_item_id '{lk.key_item_id}' is not an item.")

        # Puzzles: rewards target real entities
        for pz in self.puzzles:
            if pz.reward_type == RewardType.UNLOCK:
                require(pz.reward_target_id in lock_ids,
                        f"Puzzle '{pz.id}' unlock target '{pz.reward_target_id}' not a lock.")
            elif pz.reward_type == RewardType.REVEAL_ITEM:
                require(pz.reward_target_id in item_ids,
                        f"Puzzle '{pz.id}' reveal_item target '{pz.reward_target_id}' not an item.")
            elif pz.reward_type == RewardType.REVEAL_OBJECT:
                require(pz.reward_target_id in object_ids,
                        f"Puzzle '{pz.id}' reveal_object target '{pz.reward_target_id}' not an object.")
            # SET_FLAG targets are free-form flag names; nothing to check.

        # Player clues point to real players
        for clue in self.player_clues:
            require(clue.player_id in player_ids,
                    f"Clue references unknown player '{clue.player_id}'.")

        # Win conditions point to valid targets
        for wc in self.win_conditions:
            if wc.win_type == WinType.REACH_ROOM:
                require(wc.target_id in room_ids,
                        f"Win condition reach_room target '{wc.target_id}' not a room.")
            elif wc.win_type == WinType.OBTAIN_ITEM:
                require(wc.target_id in item_ids,
                        f"Win condition obtain_item target '{wc.target_id}' not an item.")
            # SET_FLAG win targets are free-form flag names.

        if errors:
            raise ValueError("Blueprint integrity errors:\n - " + "\n - ".join(errors))
        return self
