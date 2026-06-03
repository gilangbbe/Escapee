"""
Player action schema (the ReAct "Action" boundary).

Player LLMs must emit an action as JSON matching `PlayerAction`. The simulator
(Phase 2) only ever accepts validated `PlayerAction` objects — never free text —
which is a core anti-hallucination guard.

Fields used per verb:
  look                          -> (none)
  inspect       target_id       -> object or item id to examine
  move          direction       -> exit direction from current room
  take          target_id       -> item id to pick up
  give          target_id, to_player_id
  unlock        target_id (lock), and ONE of: value (code) / sequence (list)
                                  (key locks need no value; uses inventory)
  solve         puzzle_id, answer
  say           message          -> communication only, no world effect
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    LOOK = "look"
    INSPECT = "inspect"
    MOVE = "move"
    TAKE = "take"
    GIVE = "give"
    UNLOCK = "unlock"
    SOLVE = "solve"
    SAY = "say"


class PlayerAction(BaseModel):
    """A single structured action proposed by a player agent."""

    action: ActionType

    # Generic target (object id, item id, or lock id depending on verb).
    target_id: Optional[str] = Field(None, description="Primary target entity id.")

    # MOVE
    direction: Optional[str] = Field(None, description="Exit direction for 'move'.")

    # GIVE
    to_player_id: Optional[str] = Field(
        None, description="Recipient player id for 'give'."
    )

    # UNLOCK (code locks)
    value: Optional[str] = Field(None, description="Code entered for a code lock.")
    # UNLOCK (sequence locks)
    sequence: list[str] = Field(
        default_factory=list, description="Ordered ids for a sequence lock."
    )

    # SOLVE
    puzzle_id: Optional[str] = Field(None, description="Puzzle id for 'solve'.")
    answer: Optional[str] = Field(None, description="Answer string for 'solve'.")

    # SAY
    message: Optional[str] = Field(None, description="Spoken message for 'say'.")
