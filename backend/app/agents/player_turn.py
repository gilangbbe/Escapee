"""
Player ReAct turn schema.

A player agent emits ONE JSON object per turn matching `PlayerTurn`:

    {
      "thought": "private reasoning (not shown to other players)",
      "speak":  "optional message to the team (public)",
      "action": { ...PlayerAction... }
    }

This keeps the ReAct loop (Thought -> Speak -> Action -> Observation) on strict
rails: the engine only ever consumes `action`, validated against ground truth.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.engine.actions import PlayerAction


class PlayerTurn(BaseModel):
    """One full ReAct turn produced by a player agent."""

    thought: str = Field(
        ..., description="Private chain-of-thought reasoning for this turn."
    )
    speak: Optional[str] = Field(
        None, description="Optional public message to teammates."
    )
    action: PlayerAction = Field(
        ..., description="The structured action to execute this turn."
    )
