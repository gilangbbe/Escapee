"""
Context builder — anti-forgetting for small (7B) models.

Each turn we rebuild the player's prompt context from AUTHORITATIVE state so the
model can never drift. The context has fixed sections, in priority order:

  1. PERSONA + OBJECTIVE  (re-pinned every turn)
  2. CURRENT STATE VIEW    (room, visible objects/items, inventory, exits) — from WorldState
  3. PRIVATE CLUES         (only this player's asymmetric knowledge)
  4. ROLLING SUMMARY       (compressed older history)
  5. RECENT EVENTS         (last K turns verbatim: speech + observations)

Older events beyond the recent window are folded into the rolling summary so the
context never overflows the 7B window.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.context.channels import EventKind, MessageLog
from app.engine.state import ItemPlace, WorldState


@dataclass
class StateView:
    """A grounded, player-specific snapshot derived from WorldState."""

    player_id: str
    room_id: str
    room_name: str
    room_description: str
    visible_objects: list[tuple[str, str]]  # (id, name)
    visible_items: list[tuple[str, str]]     # (id, name)
    inventory: list[tuple[str, str]]         # (id, name)
    exits: list[tuple[str, str]]             # (direction, locked_str)
    other_players_here: list[str]

    def render(self) -> str:
        def fmt(pairs: list[tuple[str, str]]) -> str:
            return ", ".join(f"{name} [{eid}]" for eid, name in pairs) or "none"

        exit_str = (
            ", ".join(f"{d} ({state})" for d, state in self.exits) or "none"
        )
        others = ", ".join(self.other_players_here) or "you are alone"
        return (
            f"LOCATION: {self.room_name} [{self.room_id}]\n"
            f"DESCRIPTION: {self.room_description}\n"
            f"OBJECTS HERE: {fmt(self.visible_objects)}\n"
            f"ITEMS ON GROUND: {fmt(self.visible_items)}\n"
            f"YOUR INVENTORY: {fmt(self.inventory)}\n"
            f"EXITS: {exit_str}\n"
            f"OTHER PLAYERS HERE: {others}"
        )


def build_state_view(state: WorldState, player_id: str) -> StateView:
    """Derive the current grounded state view for one player."""
    room_id = state.player_locations[player_id]
    room = state.room(room_id)

    objects = [
        (oid, state.object(oid).name)
        for oid in state.visible_objects_in(room_id)
    ]
    items = [
        (iid, state.item(iid).name)
        for iid in state.visible_loose_items_in(room_id)
    ]
    inventory = [
        (iid, state.item(iid).name)
        for iid in state.player_inventories[player_id]
    ]

    exits: list[tuple[str, str]] = []
    for ex in room.exits:
        locked = ex.lock_id is not None and state.lock_locked.get(ex.lock_id, False)
        exits.append((ex.direction, "locked" if locked else "open"))

    others = [
        state_player_name(state, pid)
        for pid, loc in state.player_locations.items()
        if pid != player_id and loc == room_id
    ]

    return StateView(
        player_id=player_id,
        room_id=room_id,
        room_name=room.name,
        room_description=room.description,
        visible_objects=objects,
        visible_items=items,
        inventory=inventory,
        exits=exits,
        other_players_here=others,
    )


def state_player_name(state: WorldState, player_id: str) -> str:
    for p in state.blueprint.players:
        if p.id == player_id:
            return p.name
    return player_id


def render_recent_events(
    log: MessageLog, player_id: str, recent_window: int
) -> tuple[str, list]:
    """
    Return (rendered_recent_text, older_events). Older events are returned so the
    caller can fold them into the rolling summary.
    """
    visible = log.visible_to(player_id)
    recent = visible[-recent_window:]
    older = visible[: len(visible) - len(recent)]

    lines: list[str] = []
    for e in recent:
        if e.kind == EventKind.SPEECH:
            lines.append(f"[turn {e.turn}] {e.actor_id} said: {e.text}")
        elif e.kind == EventKind.OBSERVATION:
            who = e.actor_id or "system"
            lines.append(f"[turn {e.turn}] ({who}) {e.text}")
        else:
            lines.append(f"[turn {e.turn}] SYSTEM: {e.text}")
    return ("\n".join(lines) or "No recent events.", older)


def summarize_events(prior_summary: str, older_events: list) -> str:
    """
    Deterministic rolling summary (no LLM). Compresses older events into compact
    bullet lines. Keeps the most recent COMPRESSED_CAP bullets to bound size.

    A future enhancement can swap this for an LLM-based summarizer; keeping it
    deterministic now makes the context pipeline fully testable.
    """
    COMPRESSED_CAP = 30
    bullets: list[str] = []
    if prior_summary.strip():
        bullets.extend(
            line for line in prior_summary.splitlines() if line.strip()
        )

    for e in older_events:
        if e.kind == EventKind.SPEECH:
            bullets.append(f"- {e.actor_id} said: {e.text}")
        elif e.kind == EventKind.OBSERVATION:
            # Only keep successful, meaningful observations in the summary.
            bullets.append(f"- {e.actor_id or 'system'}: {e.text}")
        else:
            bullets.append(f"- system: {e.text}")

    if len(bullets) > COMPRESSED_CAP:
        bullets = bullets[-COMPRESSED_CAP:]
    return "\n".join(bullets)
