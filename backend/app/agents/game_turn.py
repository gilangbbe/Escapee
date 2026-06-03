"""
Player ReAct turn schema (v2 — GameSetting engine).

A player agent emits ONE JSON object per turn matching `GameTurn`:

    {
      "reflection": "optional summary on reflection checkpoints (public)",
      "hypothesis": "optional public theory + proposed next step for the team",
      "speak":  "optional message to the team (public)",
      "action": { ...GameAction... }
    }

The engine only ever consumes `action`, validated against ground truth — the
core anti-hallucination guard at the action boundary.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.engine.game_actions import GameAction


class GameTurn(BaseModel):
    """One full ReAct turn produced by a player agent (v2)."""
    reflection: Optional[str] = Field(
        None,
        description=(
            "Optional PUBLIC reflection, expected on reflection checkpoints: a "
            "short summary of what is known, what has failed, and the best next "
            "plan. Stored as the team's summarized memory."
        ),
    )
    hypothesis: Optional[str] = Field(
        None,
        description=(
            "Optional PUBLIC theory about the puzzle plus the next step you "
            "propose the team try. Shared on the team blackboard so teammates "
            "can support, critique, or counter-propose."
        ),
    )
    speak: Optional[str] = Field(
        None, description="Optional public message to teammates."
    )
    action: GameAction = Field(
        ..., description="The structured action to execute this turn."
    )


class PlanProposal(BaseModel):
    """A player's proposed (or refined) step-by-step escape plan.

    Produced during the collaborative planning phase BEFORE the game starts, so
    the team shares one ordered understanding of the objective and the steps it
    requires — instead of each agent improvising in isolation.
    """

    thought: Optional[str] = Field(
        None, description="Optional private reasoning about the plan."
    )
    plan: list[str] = Field(
        default_factory=list,
        description="Ordered, concrete steps the team should take to escape.",
    )

