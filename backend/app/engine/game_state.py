"""
Runtime GameState for the GameSetting (v2) schema — the single ground truth.

Built from a validated `GameSetting`. Holds the mutable truth of a running game:
per-object current state and location, player positions/inventories, which rooms
are accessible, active power flags, and live fuse positions.

The LLMs never mutate this directly — only `GameSimulator` handlers do, and only
through validated actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.schemas.game_setting import (
    OPEN_STATES,
    GameObject,
    GameSetting,
    ObjectState,
)


@dataclass
class GameState:
    setting: GameSetting

    object_state: dict[str, ObjectState] = field(default_factory=dict)
    object_location: dict[str, str] = field(default_factory=dict)

    player_locations: dict[str, str] = field(default_factory=dict)
    player_inventories: dict[str, list[str]] = field(default_factory=dict)

    accessible_rooms: set[str] = field(default_factory=set)
    power_flags: set[str] = field(default_factory=set)
    # Internal derived predicates used by fixed-world room-goal gates.
    condition_flags: set[str] = field(default_factory=set)
    fuse_state: dict[str, dict[str, str]] = field(default_factory=dict)

    # Power sources whose use/tool requirement has been satisfied.
    satisfied_power_sources: set[str] = field(default_factory=set)
    discovered_info: set[str] = field(default_factory=set)

    finished: bool = False
    won: bool = False
    turn: int = 0

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def from_setting(cls, setting: GameSetting) -> "GameState":
        state = cls(setting=setting)

        for obj in setting.objects:
            state.object_state[obj.id] = obj.state
            state.object_location[obj.id] = obj.location
            if obj.fuses is not None:
                state.fuse_state[obj.id] = dict(obj.fuses)

        # Players: use defined personas, or a single default solo player.
        players = setting.players or []
        start = setting.effective_start_room()
        if players:
            for p in players:
                state.player_locations[p.id] = start
                state.player_inventories[p.id] = []
        else:
            state.player_locations["player_1"] = start
            state.player_inventories["player_1"] = []

        state.accessible_rooms.add(start)
        # Recompute power flags from any fuses that start ON.
        state.recompute_power_flags()
        return state

    # ------------------------------------------------------------------ #
    # Lookups
    # ------------------------------------------------------------------ #
    def obj(self, object_id: str) -> Optional[GameObject]:
        return self.setting.object(object_id)

    def player_ids(self) -> list[str]:
        return list(self.player_locations.keys())

    def room_of(self, object_id: str) -> Optional[str]:
        """Follow the location chain until a room id is reached."""
        seen: set[str] = set()
        cur = object_id
        while cur not in seen:
            seen.add(cur)
            loc = self.object_location.get(cur)
            if loc is None:
                return None
            if loc in self.setting.rooms:
                return loc
            cur = loc
        return None

    def container_of(self, object_id: str) -> Optional[str]:
        """Return the immediate parent object id, or None if directly in a room."""
        loc = self.object_location.get(object_id)
        if loc is not None and loc not in self.setting.rooms:
            return loc
        return None

    def is_open_container(self, object_id: str) -> bool:
        return self.object_state.get(object_id) in OPEN_STATES

    # ------------------------------------------------------------------ #
    # Visibility (grounding)
    # ------------------------------------------------------------------ #
    def is_visible_to(self, object_id: str, player_id: str) -> bool:
        """Is the object currently visible to the given player?"""
        st = self.object_state.get(object_id)
        if st is None or st in (ObjectState.HIDDEN, ObjectState.TAKEN):
            return False

        # If held by a player, only that player "sees" it (in inventory).
        loc = self.object_location.get(object_id)
        if loc in self.player_inventories:
            return loc == player_id

        room = self.room_of(object_id)
        if room is None or room != self.player_locations.get(player_id):
            return False
        if room not in self.accessible_rooms:
            return False
        if st == ObjectState.LOCKED_ROOM and room not in self.accessible_rooms:
            return False

        # All ancestor containers must be open.
        parent = self.container_of(object_id)
        while parent is not None:
            if not self.is_open_container(parent):
                return False
            parent = self.container_of(parent)
        return True

    def visible_objects_for(self, player_id: str) -> list[str]:
        room = self.player_locations.get(player_id)
        out: list[str] = []
        for obj in self.setting.objects:
            # Pure connectors (synthetic gates / passages) are shown to players
            # as EXITS, not as inspectable objects — keep them out of the list.
            if obj.connects_to is not None and not obj.interactable:
                continue
            if self.room_of(obj.id) == room and self.is_visible_to(obj.id, player_id):
                out.append(obj.id)
        return out

    def exits_for(self, player_id: str) -> list[tuple[str, str, bool]]:
        """Return (door_id, to_room, is_open) for doors in the player's room."""
        room = self.player_locations.get(player_id)
        out: list[tuple[str, str, bool]] = []
        for obj in self.setting.objects:
            if (
                obj.connects_to is not None
                and self.room_of(obj.id) == room
                and self.object_state.get(obj.id) != ObjectState.HIDDEN
            ):
                is_open = self.object_state.get(obj.id) in OPEN_STATES
                out.append((obj.id, obj.connects_to, is_open))
        return out

    # ------------------------------------------------------------------ #
    # Power propagation
    # ------------------------------------------------------------------ #
    def recompute_power_flags(self) -> None:
        """Rebuild the active power-flag set from fuses + satisfied sources."""
        flags: set[str] = set()
        for obj_id, fuses in self.fuse_state.items():
            for letter, pos in fuses.items():
                if pos == "ON":
                    flags.add(f"sekring_{letter}_ON")
        # provides_power sources that have been satisfied.
        for obj in self.setting.objects:
            if obj.provides_power and obj.id in self.satisfied_power_sources:
                flags.add(obj.provides_power)
        self.power_flags = flags

        # Build a superset of boolean predicates that can be used by synthetic
        # progression gates (has_item / object_state / known_info / power_active).
        conditions = set(flags)
        for info in self.discovered_info:
            conditions.add(f"known_info_{info}")

        for inv in self.player_inventories.values():
            for item_id in inv:
                conditions.add(f"has_item_{item_id}")

        for obj_id, st in self.object_state.items():
            if st is None:
                continue
            conditions.add(f"state_{obj_id}_{st.value}")
            # Treat open/unlocked as equivalent for progression predicates.
            if st == ObjectState.OPEN:
                conditions.add(f"state_{obj_id}_{ObjectState.UNLOCKED.value}")
            elif st == ObjectState.UNLOCKED:
                conditions.add(f"state_{obj_id}_{ObjectState.OPEN.value}")

        self.condition_flags = conditions

    def apply_power_to_consumers(self) -> list[str]:
        """Auto-open any LOCKED object whose requires_power flag is now active.

        Returns the ids of objects that just unlocked.
        """
        changed: list[str] = []
        for obj in self.setting.objects:
            if (
                obj.requires_power
                and obj.requires_power in self.condition_flags
                and self.object_state.get(obj.id) in (ObjectState.LOCKED, ObjectState.LOCKED_BOLT)
            ):
                self.object_state[obj.id] = ObjectState.UNLOCKED
                changed.append(obj.id)
                if obj.connects_to:
                    self.accessible_rooms.add(obj.connects_to)
        return changed
