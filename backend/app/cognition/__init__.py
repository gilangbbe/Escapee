"""External-cognition layer for the multi-agent escape room.

The LLM agents supply *reasoning* — interpretation, hypotheses, creativity. The
deterministic modules in this package supply the *cognitive scaffolding* an LLM
is bad at holding reliably over a long game:

  - episodic memory of every attempt (action + world fingerprint + outcome),
  - a symbolic world fingerprint so we can tell when state actually changed,
  - loop detection (block doomed/redundant repeats unless the world moved),
  - a progress ledger of milestones (intermediate rewards / momentum),
  - curiosity pressure (surface untried, reachable interactions),
  - stall detection + periodic reflection scheduling,
  - a shared blackboard of teammate hypotheses.

This keeps *execution control* out of the LLM (where it drifts and loops) and in
deterministic code, exactly where escape-room cognition — state tracking, search,
loop avoidance, planning bookkeeping — belongs.
"""

from app.cognition.team_cognition import (
    AttemptRecord,
    CognitionConfig,
    TeamCognition,
    world_fingerprint,
)
from app.cognition.action_planner import ActionPlanner, PlannerDecision, PlannerOption

__all__ = [
    "AttemptRecord",
    "CognitionConfig",
    "TeamCognition",
    "ActionPlanner",
    "PlannerDecision",
    "PlannerOption",
    "world_fingerprint",
]
