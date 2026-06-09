"""
GameSimulator — the deterministic "physics engine" for the GameSetting (v2) schema.

Loads a validated `GameSetting` into `GameState` and exposes `step(player_id,
action)` which validates the action against ground truth, mutates state, and
returns a grounded `Observation`. NO LLM calls happen here; given the same state
and action the result is always identical.

Interaction model (generic requirement resolution):
  - enter_code: opens an object whose `requires_code` matches; reveals contents
    and any `reveals` target.
  - use(item -> target): satisfies `requires_tool` (item id match) or
    `requires_liquid` (item.liquid_property match); opens the target, reveals
    targets, activates `provides_power`, and opens connected rooms.
  - set_fuse: flips a fuse on a powered/open panel, recomputing power flags.
  - Power flags auto-unlock any object whose `requires_power` is satisfied.
"""

from __future__ import annotations

from app.engine.game_actions import GameAction, GameActionType
from app.engine.game_state import GameState
from app.engine.observation import Observation
from app.schemas.game_setting import GameSetting, ObjectState


def _state_satisfies(expected: ObjectState, current: ObjectState | None) -> bool:
    """Return True when current state satisfies the expected target state."""
    if current is None:
        return False
    if current == expected:
        return True
    equivalent_open_states = {ObjectState.OPEN, ObjectState.UNLOCKED}
    return expected in equivalent_open_states and current in equivalent_open_states


class GameSimulator:
    def __init__(self, setting: GameSetting) -> None:
        self.setting = setting
        self.state = GameState.from_setting(setting)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def step(self, player_id: str, action: GameAction) -> Observation:
        if self.state.finished:
            return self._fail("The game is already over.")
        if player_id not in self.state.player_locations:
            return self._fail(f"Unknown player '{player_id}'.")

        self.state.turn += 1
        handler = {
            GameActionType.LOOK: self._look,
            GameActionType.INSPECT: self._inspect,
            GameActionType.TAKE: self._take,
            GameActionType.ENTER_CODE: self._enter_code,
            GameActionType.USE: self._use,
            GameActionType.SET_FUSE: self._set_fuse,
            GameActionType.MOVE: self._move,
            GameActionType.GIVE: self._give,
            GameActionType.SAY: self._say,
        }[action.action]

        obs = handler(player_id, action)

        # Keep derived progression predicates current after every action so
        # fixed-world room-goal gates can unlock immediately once satisfied.
        self.state.recompute_power_flags()
        self.state.apply_power_to_consumers()

        if not self.state.finished and self._check_win():
            self.state.finished = True
            self.state.won = True
            obs.game_won = True
            obs.message += f" {self.setting.objective} — YOU ESCAPED!"
        return obs

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #
    def _look(self, player_id: str, _a: GameAction) -> Observation:
        room = self.state.player_locations[player_id]
        objs = self.state.visible_objects_for(player_id)
        names = ", ".join(self._tagged(o) for o in objs) or "nothing notable"
        exits = self.state.exits_for(player_id)
        exit_str = (
            ", ".join(
                f"{to} via {self._name(d)} ({'open' if op else 'locked'})"
                for d, to, op in exits
            )
            or "none"
        )
        return self._ok(
            player_id,
            f"You are in {room}. You see: {names}. Exits: {exit_str}.",
            public=False,
        )

    def _inspect(self, player_id: str, a: GameAction) -> Observation:
        tid = a.target_id
        if not tid:
            return self._fail("Inspect what? Provide target_id.", player_id)
        obj = self.state.obj(tid)
        if obj is None:
            return self._fail(f"There is no '{tid}'.", player_id)
        if not self.state.is_visible_to(tid, player_id):
            return self._fail(f"You can't see {self._name(tid)} from here.", player_id)

        desc = obj.description
        if obj.slot_description:
            desc += f" {obj.slot_description}"
        # If it's an open container, list its contents.
        if self.state.is_open_container(tid):
            contents = [
                c.id for c in self.setting.objects
                if self.state.container_of(c.id) == tid
                and self.state.object_state.get(c.id) != ObjectState.HIDDEN
            ]
            if contents:
                desc += " Inside: " + ", ".join(self._tagged(c) for c in contents) + "."
        # Record discovered info.
        if obj.contains_info:
            self.state.discovered_info.add(obj.contains_info)
        return self._ok(player_id, desc, public=False)

    def _take(self, player_id: str, a: GameAction) -> Observation:
        tid = a.target_id
        if not tid:
            return self._fail("Take what? Provide target_id.", player_id)
        obj = self.state.obj(tid)
        if obj is None:
            return self._fail(f"There is no '{tid}'.", player_id)
        if not obj.takeable:
            return self._fail(f"You can't take {self._name(tid)}.", player_id)
        if not self.state.is_visible_to(tid, player_id):
            return self._fail(f"You can't reach {self._name(tid)} right now.", player_id)

        self.state.object_location[tid] = player_id
        self.state.object_state[tid] = ObjectState.TAKEN
        self.state.player_inventories[player_id].append(tid)
        msg = f"You pick up {self._name(tid)}"
        if obj.contains_info and obj.contains_info not in self.state.discovered_info:
            self.state.discovered_info.add(obj.contains_info)
            msg += f"; you notice {obj.contains_info.replace('_', ' ')}"
        return self._ok(player_id, msg + ".")

    def _enter_code(self, player_id: str, a: GameAction) -> Observation:
        tid = a.target_id
        if not tid:
            return self._fail("Enter a code on what? Provide target_id.", player_id)
        obj = self.state.obj(tid)
        if obj is None:
            return self._fail(f"There is no '{tid}'.", player_id)
        if not self.state.is_visible_to(tid, player_id):
            return self._fail(f"You can't reach {self._name(tid)} from here.", player_id)
        if obj.requires_code is None:
            return self._fail(
                f"{self._name(tid)} has no code to enter.",
                player_id,
                hint="This object isn't code-locked. Try inspecting or using a tool on it.",
            )
        if (a.code or "").strip() != obj.requires_code:
            return self._fail(
                "That code is incorrect.",
                player_id,
                hint=(
                    "The code did not match. Re-read your clue exactly — codes are "
                    "case- and space-sensitive. If you have no code clue for this "
                    "object, the right code may belong to a teammate."
                ),
            )

        return self._open_object(player_id, tid, f"The code works. {self._name(tid)} opens.")

    def _use(self, player_id: str, a: GameAction) -> Observation:
        item_id, tid = a.item_id, a.target_id
        if not item_id or not tid:
            return self._fail("Use requires item_id and target_id.", player_id)
        if item_id not in self.state.player_inventories[player_id]:
            return self._fail(f"You aren't holding {self._name(item_id)}.", player_id)
        target = self.state.obj(tid)
        if target is None:
            return self._fail(f"There is no '{tid}'.", player_id)
        if not self.state.is_visible_to(tid, player_id):
            return self._fail(f"You can't reach {self._name(tid)} from here.", player_id)

        item = self.state.obj(item_id)
        # Tool requirement.
        if target.requires_tool == item_id:
            return self._open_object(
                player_id, tid, f"You use {self._name(item_id)} on {self._name(tid)}."
            )
        # Liquid requirement.
        if (
            target.requires_liquid is not None
            and item is not None
            and item.liquid_property == target.requires_liquid
        ):
            return self._open_object(
                player_id, tid,
                f"You apply {self._name(item_id)} to {self._name(tid)}.",
            )
        return self._fail(
            f"Using {self._name(item_id)} on {self._name(tid)} does nothing.",
            player_id,
            hint=(
                "Wrong item for this target. This object may need a different tool, "
                "a code, or power instead — inspect it for what it requires."
            ),
        )

    def _set_fuse(self, player_id: str, a: GameAction) -> Observation:
        tid, fuse, pos = a.target_id, a.fuse, (a.position or "").upper()
        if not tid or not fuse or pos not in ("ON", "OFF"):
            return self._fail("set_fuse requires target_id, fuse, position(ON/OFF).", player_id)
        if tid not in self.state.fuse_state:
            return self._fail(f"{self._name(tid)} has no fuses.", player_id)
        if not self.state.is_visible_to(tid, player_id):
            return self._fail(f"You can't reach {self._name(tid)} from here.", player_id)
        if self.state.object_state.get(tid) not in (
            ObjectState.OPEN, ObjectState.UNLOCKED, ObjectState.VISIBLE, ObjectState.POWERED
        ):
            return self._fail(f"{self._name(tid)} is sealed — open it first.", player_id)
        if fuse not in self.state.fuse_state[tid]:
            return self._fail(f"There is no fuse '{fuse}'.", player_id)

        self.state.fuse_state[tid][fuse] = pos
        self.state.recompute_power_flags()
        unlocked = self.state.apply_power_to_consumers()
        msg = f"You set fuse {fuse} to {pos}."
        if unlocked:
            msg += " You hear something unlock: " + ", ".join(self._name(u) for u in unlocked) + "."
        return self._ok(player_id, msg)

    def _move(self, player_id: str, a: GameAction) -> Observation:
        dest = a.to_room or a.target_id
        if not dest:
            return self._fail("Move where? Provide to_room.", player_id)
        for door_id, to_room, is_open in self.state.exits_for(player_id):
            if to_room == dest:
                if not is_open:
                    return self._fail(
                        f"The way to {dest} ({self._name(door_id)}) is locked.",
                        player_id,
                        hint=(
                            f"{self._name(door_id)} must be unlocked first — it may need "
                            "a code, a tool/key, or power before anyone can pass."
                        ),
                    )
                self.state.player_locations[player_id] = dest
                self.state.accessible_rooms.add(dest)
                return self._ok(player_id, f"You move into {dest}.")
        return self._fail(
            f"There is no open route to '{dest}' from here.",
            player_id,
            hint=(
                "You can only move through an OPEN exit listed in your current state. "
                "Open a connecting door first, or pick a room you can actually reach."
            ),
        )

    def _give(self, player_id: str, a: GameAction) -> Observation:
        item_id, other = a.item_id or a.target_id, a.to_player_id
        if not item_id or not other:
            return self._fail("Give requires item_id and to_player_id.", player_id)
        recipient_id = self._resolve_player_id(other)
        if recipient_id is None:
            return self._fail(f"Unknown player '{other}'.", player_id)
        if item_id not in self.state.player_inventories[player_id]:
            return self._fail(f"You aren't holding {self._name(item_id)}.", player_id)
        if self.state.player_locations[player_id] != self.state.player_locations[recipient_id]:
            return self._fail("You must share a room to hand something over.", player_id)
        self.state.player_inventories[player_id].remove(item_id)
        self.state.player_inventories[recipient_id].append(item_id)
        self.state.object_location[item_id] = recipient_id
        return self._ok(
            player_id,
            f"You give {self._name(item_id)} to {self._player_name(recipient_id)}.",
        )

    def _say(self, player_id: str, a: GameAction) -> Observation:
        return self._ok(
            player_id, f'{self._player_name(player_id)} says: "{a.message or ""}"', public=True
        )

    # ------------------------------------------------------------------ #
    # Shared open/reveal logic
    # ------------------------------------------------------------------ #
    def _open_object(self, player_id: str, object_id: str, base_msg: str) -> Observation:
        obj = self.state.obj(object_id)
        # Transition the object to an open/unlocked state.
        if obj.connects_to is not None:
            self.state.object_state[object_id] = ObjectState.UNLOCKED
            self.state.accessible_rooms.add(obj.connects_to)
        else:
            self.state.object_state[object_id] = ObjectState.OPEN

        extras: list[str] = []

        # Opening/unlocking a container reveals the information it holds, exactly
        # as inspecting it would. Without this, an object solved via USE/ENTER_CODE
        # (e.g. a console unlocked with a tool) would keep its `contains_info`
        # hidden, stalling any downstream lock or known_info gate that needs it.
        if obj.contains_info and obj.contains_info not in self.state.discovered_info:
            self.state.discovered_info.add(obj.contains_info)
            extras.append(f"you learn {obj.contains_info.replace('_', ' ')}")

        # Reveal a hidden target.
        if obj.reveals is not None:
            self.state.object_state[obj.reveals] = ObjectState.VISIBLE
            extras.append(f"{self._name(obj.reveals)} is now visible")

        # Activate a power source.
        if obj.provides_power is not None:
            self.state.satisfied_power_sources.add(object_id)
            self.state.recompute_power_flags()
            unlocked = self.state.apply_power_to_consumers()
            if unlocked:
                extras.append("power restored to " + ", ".join(self._name(u) for u in unlocked))

        msg = base_msg
        if extras:
            msg += " (" + "; ".join(extras) + ".)"
        return self._ok(player_id, msg)

    # ------------------------------------------------------------------ #
    # Win evaluation
    # ------------------------------------------------------------------ #
    def _check_win(self) -> bool:
        wc = self.setting.win_condition
        return _state_satisfies(wc.state, self.state.object_state.get(wc.object_id))

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _name(self, object_id: str) -> str:
        obj = self.state.obj(object_id)
        return object_id if obj is None else object_id  # ids are human-readable here

    def _tagged(self, object_id: str) -> str:
        return f"{object_id}"

    def _player_name(self, player_id: str) -> str:
        for p in self.setting.players:
            if p.id == player_id:
                return p.name
        return player_id

    def _resolve_player_id(self, token: str) -> str | None:
        """Resolve a player reference by id or display name (case-insensitive)."""
        cleaned = (token or "").strip()
        if not cleaned:
            return None
        if cleaned in self.state.player_locations:
            return cleaned

        low = cleaned.lower()
        for pid in self.state.player_locations:
            if pid.lower() == low:
                return pid
        for persona in self.setting.players:
            if persona.name.lower() == low:
                return persona.id
        return None

    def _room_fields(self, player_id: str | None) -> dict:
        if player_id is None:
            return {}
        return {
            "room_id": self.state.player_locations.get(player_id),
            "visible_objects": self.state.visible_objects_for(player_id),
            "exits": [to for _d, to, _o in self.state.exits_for(player_id)],
            "inventory": list(self.state.player_inventories.get(player_id, [])),
        }

    def _ok(self, player_id: str, message: str, public: bool = True) -> Observation:
        return Observation(success=True, message=message, public=public,
                           **self._room_fields(player_id))

    def _fail(self, message: str, player_id: str | None = None, hint: str | None = None) -> Observation:
        return Observation(success=False, message=message, public=False, hint=hint,
                           **self._room_fields(player_id))
