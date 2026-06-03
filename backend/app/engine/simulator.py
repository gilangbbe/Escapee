"""
WorldSimulator — the deterministic "physics engine".

Loads a validated blueprint into `WorldState` and exposes `step(player_id,
action)` which validates the action against ground truth, mutates state, and
returns a grounded `Observation`. NO LLM calls happen here. Given the same state
and action, the result is always identical.
"""

from __future__ import annotations

from app.engine.actions import ActionType, PlayerAction
from app.engine.observation import Observation
from app.engine.state import ItemPlace, WorldState
from app.schemas.blueprint import LockType, RewardType, RoomBlueprint, WinType


class WorldSimulator:
    def __init__(self, blueprint: RoomBlueprint) -> None:
        self.blueprint = blueprint
        self.state = WorldState.from_blueprint(blueprint)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def step(self, player_id: str, action: PlayerAction) -> Observation:
        """Validate and apply one action; return a grounded observation."""
        if self.state.finished:
            return self._fail("The game is already over.")

        if player_id not in self.state.player_locations:
            return self._fail(f"Unknown player '{player_id}'.")

        self.state.turn += 1

        handler = {
            ActionType.LOOK: self._look,
            ActionType.INSPECT: self._inspect,
            ActionType.MOVE: self._move,
            ActionType.TAKE: self._take,
            ActionType.GIVE: self._give,
            ActionType.UNLOCK: self._unlock,
            ActionType.SOLVE: self._solve,
            ActionType.SAY: self._say,
        }[action.action]

        obs = handler(player_id, action)

        # After any action, re-check win conditions.
        if not self.state.finished and self._check_win():
            self.state.finished = True
            self.state.won = True
            obs.game_won = True
            obs.message += " " + self._victory_text()
        return obs

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #
    def _look(self, player_id: str, _action: PlayerAction) -> Observation:
        room_id = self.state.player_locations[player_id]
        room = self.state.room(room_id)
        objs = self.state.visible_objects_in(room_id)
        items = self.state.visible_loose_items_in(room_id)
        exits = self.state.exit_directions(room_id)
        obj_names = ", ".join(self._name(o) for o in objs) or "nothing notable"
        item_names = ", ".join(self._name(i) for i in items) or "no loose items"
        return self._ok(
            player_id,
            f"{room.name}: {room.description} You see: {obj_names}. "
            f"On the ground: {item_names}. Exits: {', '.join(exits) or 'none'}.",
            public=False,
        )

    def _inspect(self, player_id: str, action: PlayerAction) -> Observation:
        tid = action.target_id
        if not tid:
            return self._fail("Inspect what? Provide target_id.", player_id)
        room_id = self.state.player_locations[player_id]

        # Inspect an object in the room.
        obj = self.state.object(tid)
        if obj is not None:
            room = self.state.room(room_id)
            if tid not in room.objects or self.state.object_hidden.get(tid, False):
                return self._fail(f"There is no {self._name(tid)} here.", player_id)
            desc = obj.description
            if obj.is_container:
                lock = self.state.lock(obj.lock_id) if obj.lock_id else None
                if lock and self.state.lock_locked.get(lock.id, False):
                    desc += f" It is locked. ({lock.hint or 'No hint.'})"
                else:
                    contents = [
                        c for c in obj.contains
                        if self.state.item_locations[c].place == ItemPlace.CONTAINER
                        and self.state.item_locations[c].container_id == tid
                        and not self.state.item_hidden.get(c, False)
                    ]
                    names = ", ".join(self._name(c) for c in contents) or "nothing"
                    desc += f" Inside you see: {names}."
            return self._ok(player_id, desc, public=False)

        # Inspect an item (in room, in this player's inventory, or open container here).
        item = self.state.item(tid)
        if item is not None:
            if self._item_inspectable(player_id, tid):
                return self._ok(player_id, item.description, public=False)
            return self._fail(f"You can't see a {self._name(tid)} to inspect.", player_id)

        return self._fail(f"There is no '{tid}' to inspect.", player_id)

    def _move(self, player_id: str, action: PlayerAction) -> Observation:
        direction = action.direction or action.target_id
        if not direction:
            return self._fail("Move where? Provide a direction.", player_id)
        room_id = self.state.player_locations[player_id]
        room = self.state.room(room_id)
        for ex in room.exits:
            if ex.direction == direction:
                if ex.lock_id and self.state.lock_locked.get(ex.lock_id, False):
                    lock = self.state.lock(ex.lock_id)
                    return self._fail(
                        f"The {direction} is locked. ({lock.hint or 'No hint.'})",
                        player_id,
                    )
                self.state.player_locations[player_id] = ex.to_room_id
                dest = self.state.room(ex.to_room_id)
                return self._ok(
                    player_id,
                    f"You move {direction} into {dest.name}. {dest.description}",
                )
        return self._fail(f"There is no exit '{direction}' here.", player_id)

    def _take(self, player_id: str, action: PlayerAction) -> Observation:
        tid = action.target_id
        if not tid:
            return self._fail("Take what? Provide target_id.", player_id)
        item = self.state.item(tid)
        if item is None:
            return self._fail(f"There is no '{tid}'.", player_id)
        if self.state.item_hidden.get(tid, False):
            return self._fail(f"You don't see a {self._name(tid)} here.", player_id)

        loc = self.state.item_locations[tid]
        room_id = self.state.player_locations[player_id]

        # Loose in the current room.
        if loc.place == ItemPlace.ROOM and loc.room_id == room_id:
            self._move_to_inventory(tid, player_id)
            return self._ok(player_id, f"You pick up the {self._name(tid)}.")

        # Inside an OPEN container in this room.
        if loc.place == ItemPlace.CONTAINER:
            container = self.state.object(loc.container_id)
            if (
                container
                and loc.container_id in self.state.room(room_id).objects
                and self._container_open(loc.container_id)
            ):
                self._move_to_inventory(tid, player_id)
                return self._ok(
                    player_id,
                    f"You take the {self._name(tid)} from the {self._name(loc.container_id)}.",
                )
            return self._fail(
                f"The {self._name(tid)} is inside something you haven't opened.",
                player_id,
            )

        if loc.place == ItemPlace.INVENTORY:
            return self._fail(f"Someone already holds the {self._name(tid)}.", player_id)

        return self._fail(f"You can't take the {self._name(tid)} right now.", player_id)

    def _give(self, player_id: str, action: PlayerAction) -> Observation:
        tid = action.target_id
        other = action.to_player_id
        if not tid or not other:
            return self._fail("Give requires target_id and to_player_id.", player_id)
        if other not in self.state.player_locations:
            return self._fail(f"Unknown player '{other}'.", player_id)
        if tid not in self.state.player_inventories[player_id]:
            return self._fail(f"You aren't holding the {self._name(tid)}.", player_id)
        if self.state.player_locations[player_id] != self.state.player_locations[other]:
            return self._fail("You must be in the same room to hand something over.", player_id)
        self.state.player_inventories[player_id].remove(tid)
        self.state.player_inventories[other].append(tid)
        self.state.item_locations[tid].player_id = other
        return self._ok(
            player_id,
            f"You give the {self._name(tid)} to {self._player_name(other)}.",
        )

    def _unlock(self, player_id: str, action: PlayerAction) -> Observation:
        lid = action.target_id
        if not lid:
            return self._fail("Unlock what? Provide the lock's target_id.", player_id)
        lock = self.state.lock(lid)
        if lock is None:
            return self._fail(f"There is no lock '{lid}'.", player_id)
        if not self.state.lock_locked.get(lid, False):
            return self._ok(player_id, f"The {self._name(lid)} is already unlocked.")

        # Lock must be reachable from the player's current room.
        if not self._lock_reachable(player_id, lid):
            return self._fail("You can't reach that lock from here.", player_id)

        if lock.lock_type == LockType.KEY:
            if lock.key_item_id in self.state.player_inventories[player_id]:
                self.state.lock_locked[lid] = False
                return self._unlocked_obs(player_id, lid)
            return self._fail(
                f"You need the right key for the {self._name(lid)}.", player_id
            )

        if lock.lock_type == LockType.CODE:
            if action.value is not None and action.value.strip() == lock.code_solution:
                self.state.lock_locked[lid] = False
                return self._unlocked_obs(player_id, lid)
            return self._fail("That code is incorrect.", player_id)

        if lock.lock_type == LockType.SEQUENCE:
            if action.sequence == lock.sequence_solution:
                # Any item-typed element of the sequence must be held by the player.
                missing = [
                    s for s in lock.sequence_solution
                    if self.state.item(s) is not None
                    and s not in self.state.player_inventories[player_id]
                ]
                if missing:
                    return self._fail(
                        f"You lack the required item(s): "
                        f"{', '.join(self._name(m) for m in missing)}.",
                        player_id,
                    )
                self.state.lock_locked[lid] = False
                return self._unlocked_obs(player_id, lid)
            return self._fail("That sequence doesn't work.", player_id)

        return self._fail("This lock can't be opened that way.", player_id)

    def _solve(self, player_id: str, action: PlayerAction) -> Observation:
        pid = action.puzzle_id
        if not pid:
            return self._fail("Solve which puzzle? Provide puzzle_id.", player_id)
        puzzle = self.state.puzzle(pid)
        if puzzle is None:
            return self._fail(f"There is no puzzle '{pid}'.", player_id)
        if pid in self.state.solved_puzzles:
            return self._ok(player_id, "That puzzle is already solved.")
        if (action.answer or "").strip().lower() != puzzle.solution.strip().lower():
            return self._fail("That's not the right answer.", player_id)

        self.state.solved_puzzles.add(pid)
        return self._apply_reward(player_id, puzzle)

    def _say(self, player_id: str, action: PlayerAction) -> Observation:
        msg = action.message or ""
        return self._ok(
            player_id,
            f"{self._player_name(player_id)} says: \"{msg}\"",
            public=True,
        )

    # ------------------------------------------------------------------ #
    # Reward application
    # ------------------------------------------------------------------ #
    def _apply_reward(self, player_id: str, puzzle) -> Observation:
        target = puzzle.reward_target_id
        if puzzle.reward_type == RewardType.UNLOCK:
            self.state.lock_locked[target] = False
            return self._ok(player_id, f"Solved! The {self._name(target)} unlocks.")
        if puzzle.reward_type == RewardType.REVEAL_ITEM:
            self.state.item_hidden[target] = False
            return self._ok(player_id, f"Solved! A {self._name(target)} is now revealed.")
        if puzzle.reward_type == RewardType.REVEAL_OBJECT:
            self.state.object_hidden[target] = False
            return self._ok(player_id, f"Solved! A {self._name(target)} appears.")
        if puzzle.reward_type == RewardType.SET_FLAG:
            self.state.flags[target] = True
            return self._ok(player_id, f"Solved! ({target} is now set.)")
        return self._fail("The puzzle reward could not be applied.", player_id)

    # ------------------------------------------------------------------ #
    # Win evaluation
    # ------------------------------------------------------------------ #
    def _check_win(self) -> bool:
        for wc in self.blueprint.win_conditions:
            if wc.win_type == WinType.REACH_ROOM:
                if any(
                    loc == wc.target_id
                    for loc in self.state.player_locations.values()
                ):
                    return True
            elif wc.win_type == WinType.OBTAIN_ITEM:
                if any(
                    wc.target_id in inv
                    for inv in self.state.player_inventories.values()
                ):
                    return True
            elif wc.win_type == WinType.SET_FLAG:
                if self.state.flags.get(wc.target_id, False):
                    return True
        return False

    def _victory_text(self) -> str:
        descs = [wc.description for wc in self.blueprint.win_conditions if wc.description]
        return "YOU ESCAPED! " + (descs[0] if descs else "")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _move_to_inventory(self, item_id: str, player_id: str) -> None:
        loc = self.state.item_locations[item_id]
        if loc.place == ItemPlace.CONTAINER and loc.container_id:
            container = self.state.object(loc.container_id)
            if container and item_id in container.contains:
                # Leave blueprint immutable; container view filters by location.
                pass
        loc.place = ItemPlace.INVENTORY
        loc.player_id = player_id
        loc.room_id = None
        loc.container_id = None
        if item_id not in self.state.player_inventories[player_id]:
            self.state.player_inventories[player_id].append(item_id)

    def _container_open(self, container_id: str) -> bool:
        container = self.state.object(container_id)
        if container is None:
            return False
        if container.lock_id is None:
            return True
        return not self.state.lock_locked.get(container.lock_id, False)

    def _item_inspectable(self, player_id: str, item_id: str) -> bool:
        if self.state.item_hidden.get(item_id, False):
            return False
        loc = self.state.item_locations[item_id]
        room_id = self.state.player_locations[player_id]
        if loc.place == ItemPlace.INVENTORY and loc.player_id == player_id:
            return True
        if loc.place == ItemPlace.ROOM and loc.room_id == room_id:
            return True
        if loc.place == ItemPlace.CONTAINER and loc.container_id:
            return (
                loc.container_id in self.state.room(room_id).objects
                and self._container_open(loc.container_id)
            )
        return False

    def _lock_reachable(self, player_id: str, lock_id: str) -> bool:
        room_id = self.state.player_locations[player_id]
        room = self.state.room(room_id)
        # Lock on an exit of the current room.
        if any(ex.lock_id == lock_id for ex in room.exits):
            return True
        # Lock on a visible container in the current room.
        for oid in room.objects:
            obj = self.state.object(oid)
            if obj and obj.lock_id == lock_id and not self.state.object_hidden.get(oid, False):
                return True
        return False

    def _unlocked_obs(self, player_id: str, lock_id: str) -> Observation:
        return self._ok(player_id, f"The {self._name(lock_id)} unlocks with a clunk.")

    def _name(self, entity_id: str) -> str:
        for getter in (self.state.object, self.state.item, self.state.lock):
            ent = getter(entity_id)
            if ent is not None:
                return getattr(ent, "name", entity_id)
        return entity_id

    def _player_name(self, player_id: str) -> str:
        for p in self.blueprint.players:
            if p.id == player_id:
                return p.name
        return player_id

    def _room_view_fields(self, player_id: str) -> dict:
        room_id = self.state.player_locations[player_id]
        return {
            "room_id": room_id,
            "visible_objects": self.state.visible_objects_in(room_id),
            "visible_items": self.state.visible_loose_items_in(room_id),
            "exits": self.state.exit_directions(room_id),
            "inventory": list(self.state.player_inventories[player_id]),
        }

    def _ok(self, player_id: str, message: str, public: bool = True) -> Observation:
        return Observation(success=True, message=message, public=public,
                           **self._room_view_fields(player_id))

    def _fail(self, message: str, player_id: str | None = None) -> Observation:
        fields = self._room_view_fields(player_id) if player_id else {}
        return Observation(success=False, message=message, public=False, **fields)
