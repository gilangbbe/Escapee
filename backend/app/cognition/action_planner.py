"""Deterministic planner-as-tool for long-horizon anti-stall control.

The planner evaluates a small policy candidate set with short-horizon forward
search on cloned simulator states. It returns ranked actions plus a best action
that the orchestrator can use for strict fallback when the LLM is stalled.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from app.cognition.team_cognition import (
    TeamCognition,
    action_signature,
    compute_milestones,
    derive_board,
)
from app.engine.game_actions import GameAction, GameActionType
from app.engine.game_simulator import GameSimulator
from app.engine.game_state import GameState
from app.engine.observation import Observation


_FREE_ACTIONS = {GameActionType.SAY, GameActionType.LOOK}


@dataclass(frozen=True)
class PlannerOption:
    action: GameAction
    score: float
    rationale: str


@dataclass(frozen=True)
class PlannerDecision:
    best_action: GameAction
    ranked: list[PlannerOption]

    @property
    def best_progress_action(self) -> GameAction | None:
        for option in self.ranked:
            if option.action.action not in _FREE_ACTIONS and option.score > 0:
                return option.action
        return None


@dataclass
class ActionPlanner:
    """Deterministic planner for fallback and anti-stall escalation."""

    depth: int = 3
    beam_width: int = 5
    discount: float = 0.75

    def plan_turn(
        self,
        *,
        player_id: str,
        state: GameState,
        cognition: TeamCognition,
        current_goal: str,
        next_plan_step: str = "",
    ) -> PlannerDecision:
        actions = cognition.policy_candidates(
            player_id,
            state,
            current_goal,
            next_plan_step,
        )
        if not actions:
            fallback = GameAction(action=GameActionType.LOOK)
            return PlannerDecision(
                best_action=fallback,
                ranked=[PlannerOption(action=fallback, score=-1.0, rationale="No deterministic candidates")],
            )

        ranked: list[PlannerOption] = []
        for action in actions[: self.beam_width]:
            immediate, next_state, obs = self._simulate(state, player_id, action)
            future = 0.0
            if self.depth > 1 and obs.success and not obs.game_won and not next_state.finished:
                future = self._search(
                    player_id=player_id,
                    state=next_state,
                    cognition=cognition,
                    current_goal=current_goal,
                    next_plan_step=next_plan_step,
                    depth=self.depth - 1,
                    path={action_signature(action)},
                )
            score = immediate + (self.discount * future)
            ranked.append(
                PlannerOption(
                    action=action,
                    score=score,
                    rationale=self._rationale(action, obs, immediate),
                )
            )

        ranked.sort(key=lambda x: x.score, reverse=True)
        return PlannerDecision(best_action=ranked[0].action, ranked=ranked[:3])

    def _search(
        self,
        *,
        player_id: str,
        state: GameState,
        cognition: TeamCognition,
        current_goal: str,
        next_plan_step: str,
        depth: int,
        path: set[str],
    ) -> float:
        if depth <= 0 or state.finished:
            return 0.0

        actions = cognition.policy_candidates(player_id, state, current_goal, next_plan_step)
        if not actions:
            return 0.0

        best = float("-inf")
        for action in actions[: self.beam_width]:
            sig = action_signature(action)
            repeat_penalty = 2.5 if sig in path else 0.0
            immediate, next_state, obs = self._simulate(state, player_id, action)
            score = immediate - repeat_penalty
            if depth > 1 and obs.success and not obs.game_won and not next_state.finished:
                score += self.discount * self._search(
                    player_id=player_id,
                    state=next_state,
                    cognition=cognition,
                    current_goal=current_goal,
                    next_plan_step=next_plan_step,
                    depth=depth - 1,
                    path=path | {sig},
                )
            if score > best:
                best = score
        return 0.0 if best == float("-inf") else best

    def _simulate(
        self,
        state: GameState,
        player_id: str,
        action: GameAction,
    ) -> tuple[float, GameState, Observation]:
        sim = GameSimulator(state.setting)
        sim.state = deepcopy(state)

        before_board = derive_board(sim.state)
        before_ms = compute_milestones(sim.state)

        obs = sim.step(player_id, action)

        after_board = derive_board(sim.state)
        after_ms = compute_milestones(sim.state)
        score = self._transition_score(
            action=action,
            obs=obs,
            unsolved_before=len(before_board.unsolved),
            unsolved_after=len(after_board.unsolved),
            new_ms=len(after_ms - before_ms),
        )
        return score, sim.state, obs

    def _transition_score(
        self,
        *,
        action: GameAction,
        obs: Observation,
        unsolved_before: int,
        unsolved_after: int,
        new_ms: int,
    ) -> float:
        score = 0.0
        if obs.game_won:
            score += 120.0
        score += 3.0 if obs.success else -5.0

        if action.action in _FREE_ACTIONS:
            score -= 2.0

        if unsolved_after < unsolved_before:
            score += 18.0 * (unsolved_before - unsolved_after)
        elif unsolved_after > unsolved_before:
            score -= 4.0

        if new_ms > 0:
            score += 8.0 * new_ms
        elif obs.success and action.action not in _FREE_ACTIONS:
            score -= 1.0

        return score

    def _rationale(self, action: GameAction, obs: Observation, score: float) -> str:
        tag = "success" if obs.success else "failed"
        return f"{tag}; score={score:.1f}; action={action_signature(action)}"
