"""
Observation — the grounded result of an action.

Every action the simulator processes returns an Observation. This is the truth
fed back to the player LLM each turn, correcting any drift in its mental model.
Observations are derived ONLY from authoritative world state, never from the
model's claims.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Observation(BaseModel):
    """Structured, grounded feedback for a single action."""

    success: bool = Field(..., description="Did the action take effect?")
    message: str = Field(..., description="Narrative, in-world description of the result.")

    # Optional actionable guidance, derived from ground truth (never leaks the
    # solution). Helps the agent reason about WHY an action failed and what
    # category of move might work next.
    hint: str | None = Field(
        None, description="Grounded, non-spoiler guidance about what to try next."
    )

    # Optional structured payloads the context builder / UI can use.
    room_id: str | None = Field(None, description="Room the actor is in after the action.")
    visible_objects: list[str] = Field(default_factory=list)
    visible_items: list[str] = Field(default_factory=list)
    exits: list[str] = Field(default_factory=list)
    inventory: list[str] = Field(default_factory=list)

    # Public vs private routing (Phase 3/4 will use this).
    public: bool = Field(
        True, description="If true, broadcast to all players; else only the actor."
    )

    game_won: bool = Field(False, description="True once a win condition is satisfied.")
