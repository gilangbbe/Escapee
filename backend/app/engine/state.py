"""
Runtime world state — the single source of ground truth.

`WorldState` is built from a validated `RoomBlueprint` and holds the mutable
truth of the simulation: where every item is, which locks are open, which hidden
entities have been revealed, player positions/inventories, and world flags.

The LLMs never mutate this directly. Only `WorldSimulator` action handlers do,
and only through validated `PlayerAction`s.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from app.schemas.blueprint import RoomBlueprint


class ItemPlace(str, Enum):
    """Where an item currently lives."""

    ROOM = "room"            # loose on the floor of a room
    CONTAINER = "container"  # inside a container object
    INVENTORY = "inventory"  # held by a player
    REMOVED = "removed"      # consumed / out of play


@dataclass
class ItemLocation:
    """Tracks the location of a single item."""

    place: ItemPlace
    room_id: Optional[str] = None       # when place == ROOM
    container_id: Optional[str] = None  # when place == CONTAINER
    player_id: Optional[str] = None     # when place == INVENTORY


@dataclass
class WorldState:
    """Mutable ground-truth state of a running game."""

    blueprint: RoomBlueprint

    # Player positions and inventories.
    player_locations: dict[str, str] = field(default_factory=dict)
    player_inventories: dict[str, list[str]] = field(default_factory=dict)

    # Lock states: lock_id -> locked(bool).
    lock_locked: dict[str, bool] = field(default_factory=dict)

    # Visibility of hidden entities.
    item_hidden: dict[str, bool] = field(default_factory=dict)
    object_hidden: dict[str, bool] = field(default_factory=dict)

    # Item locations.
    item_locations: dict[str, ItemLocation] = field(default_factory=dict)

    # Arbitrary world flags (set by SET_FLAG puzzle rewards / win conditions).
    flags: dict[str, bool] = field(default_factory=dict)

    # Progress tracking.
    solved_puzzles: set[str] = field(default_factory=set)
    opened_containers: set[str] = field(default_factory=set)

    finished: bool = False
    won: bool = False
    turn: int = 0

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def from_blueprint(cls, bp: RoomBlueprint) -> "WorldState":
        state = cls(blueprint=bp)

        # Players start in the start room with empty inventories.
        for p in bp.players:
            state.player_locations[p.id] = bp.start_room_id
            state.player_inventories[p.id] = []

        # Lock initial states.
        for lk in bp.locks:
            state.lock_locked[lk.id] = lk.locked

        # Hidden flags.
        for it in bp.items:
            state.item_hidden[it.id] = it.hidden
        for ob in bp.objects:
            state.object_hidden[ob.id] = ob.hidden

        # Item locations: loose-in-room first, then container contents.
        for room in bp.rooms:
            for iid in room.items:
                state.item_locations[iid] = ItemLocation(
                    place=ItemPlace.ROOM, room_id=room.id
                )
        for obj in bp.objects:
            for iid in obj.contains:
                state.item_locations[iid] = ItemLocation(
                    place=ItemPlace.CONTAINER, container_id=obj.id
                )

        # Any item never placed defaults to REMOVED (shouldn't happen given
        # the integrity validator, but keeps state total).
        for it in bp.items:
            state.item_locations.setdefault(
                it.id, ItemLocation(place=ItemPlace.REMOVED)
            )

        return state

    # ------------------------------------------------------------------ #
    # Convenience lookups (read-only views over blueprint + state)
    # ------------------------------------------------------------------ #
    def room(self, room_id: str):
        for r in self.blueprint.rooms:
            if r.id == room_id:
                return r
        return None

    def object(self, object_id: str):
        for o in self.blueprint.objects:
            if o.id == object_id:
                return o
        return None

    def item(self, item_id: str):
        for i in self.blueprint.items:
            if i.id == item_id:
                return i
        return None

    def lock(self, lock_id: str):
        for lk in self.blueprint.locks:
            if lk.id == lock_id:
                return lk
        return None

    def puzzle(self, puzzle_id: str):
        for p in self.blueprint.puzzles:
            if p.id == puzzle_id:
                return p
        return None

    def visible_objects_in(self, room_id: str) -> list[str]:
        room = self.room(room_id)
        if room is None:
            return []
        return [oid for oid in room.objects if not self.object_hidden.get(oid, False)]

    def visible_loose_items_in(self, room_id: str) -> list[str]:
        result = []
        for iid, loc in self.item_locations.items():
            if (
                loc.place == ItemPlace.ROOM
                and loc.room_id == room_id
                and not self.item_hidden.get(iid, False)
            ):
                result.append(iid)
        return result

    def exit_directions(self, room_id: str) -> list[str]:
        room = self.room(room_id)
        if room is None:
            return []
        return [ex.direction for ex in room.exits]
