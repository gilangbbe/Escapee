"""
Conversation / event channels.

Keeps the public (shared) chat + the global event log, and per-player private
clue stores. The context builder (Phase 3) reads these to assemble each turn's
prompt; the orchestrator (Phase 4) writes to them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EventKind(str, Enum):
    SPEECH = "speech"            # a player said something (public)
    OBSERVATION = "observation"  # grounded result of an action
    SYSTEM = "system"            # narrator / system message
    PLANNER = "planner"          # deterministic planner telemetry (observer/debug)
    PROMPT = "prompt"            # model prompt shown for debugging / UI visibility
    DECISION = "decision"        # validated player-turn JSON (debug visibility)
    NARRATION = "narration"      # GM prose story (observer-only; not seen by agents)
    HUMAN_TURN = "human_turn"    # waiting for human player input (carries candidate actions)


@dataclass
class Event:
    """A single timeline entry."""

    kind: EventKind
    actor_id: str | None        # player id, or None for system
    text: str
    turn: int
    public: bool = True
    # If not public, only this player sees it (private observation).
    audience_id: str | None = None
    # Optional structured telemetry payload (observer/debug use).
    data: dict | None = None


@dataclass
class MessageLog:
    """Ordered event timeline + per-player private clues."""

    events: list[Event] = field(default_factory=list)
    private_clues: dict[str, list[str]] = field(default_factory=dict)

    def add(self, event: Event) -> None:
        self.events.append(event)

    def add_clue(self, player_id: str, clue: str) -> None:
        self.private_clues.setdefault(player_id, []).append(clue)

    def visible_to(self, player_id: str) -> list[Event]:
        """Events this player is allowed to see (public + their own private)."""
        out: list[Event] = []
        for e in self.events:
            if e.public:
                out.append(e)
            elif e.audience_id == player_id or e.actor_id == player_id:
                out.append(e)
        return out
