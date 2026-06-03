"""
GamePlayerAgent (v2) — persona + context + LLM + JSON repair into one ReAct turn.

`decide()` assembles the per-turn prompt from the authoritative `GameState`,
calls the local 7B model via Ollama (json mode), and parses/repairs the output
into a validated `GameTurn`. The orchestrator (Phase 4) takes the resulting
`GameAction`, feeds it to the `GameSimulator`, and writes the Observation back
into the `MessageLog`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.agents.game_player_prompt import (
    build_player_system_prompt,
    build_player_user_prompt,
    build_plan_system_prompt,
    build_plan_user_prompt,
    primary_objective,
)
from app.agents.game_turn import GameTurn, PlanProposal
from app.cognition.team_cognition import action_signature, derive_board
from app.context.game_builder import (
    TeamBrief,
    build_game_state_view,
)
from app.context.channels import MessageLog
from app.engine.game_state import GameState
from app.llm.json_repair import ParseResult, parse_and_validate
from app.llm.ollama_client import OllamaClient
from app.schemas.game_setting import PlayerPersona


@dataclass
class GamePlayerAgent:
    """A single player's persona, model binding, and rolling-summary memory."""

    persona: PlayerPersona
    client: OllamaClient
    recent_window: int = 8
    max_attempts: int = 3
    temperature: float = 0.7
    action_memory: int = 6

    # Per-player log of the action signatures this agent has emitted.
    _own_actions: list[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self._system_prompt = build_player_system_prompt(self.persona)

    async def propose_plan(
        self, state: GameState, log: MessageLog, draft_plan: list[str] | None = None
    ) -> list[str]:
        """Collaborative planning phase: propose or refine the team's escape plan.

        Returns the ordered steps. Falls back to any `draft_plan` (then to the
        empty plan) if the model fails, so planning can never stall the game.
        """
        view = build_game_state_view(state, self.persona.id)
        board = derive_board(state)
        messages = [
            {"role": "system", "content": build_plan_system_prompt(self.persona)},
            {
                "role": "user",
                "content": build_plan_user_prompt(
                    objective=primary_objective(state.setting),
                    state_view_text=view.render(),
                    private_clues=log.private_clues.get(self.persona.id, []),
                    open_puzzles=board.unsolved,
                    draft_plan=draft_plan,
                ),
            },
        ]
        try:
            raw = await self.client.chat(
                messages,
                format_schema=PlanProposal.model_json_schema(),
                temperature=self.temperature,
            )
            result = parse_and_validate(raw, PlanProposal)
            if result.ok and result.value is not None and result.value.plan:
                return [s.strip() for s in result.value.plan if s and s.strip()]
        except Exception:
            pass
        return list(draft_plan or [])

    def render_prompt(self, messages: list[dict[str, str]]) -> str:
        """Render the exact prompt payload passed to the model for UI/debugging."""
        chunks: list[str] = []
        for message in messages:
            role = message.get("role", "?").upper()
            content = message.get("content", "")
            chunks.append(f"[{role}]\n{content}")
        return "\n\n---\n\n".join(chunks)

    def build_messages(
        self, state: GameState, log: MessageLog, team_brief: TeamBrief | None = None
    ) -> list[dict[str, str]]:
        """Assemble the full prompt for this turn from authoritative state."""
        view = build_game_state_view(state, self.persona.id)

        user = build_player_user_prompt(
            objective=primary_objective(state.setting),
            state_view_text=view.render(),
            private_clues=log.private_clues.get(self.persona.id, []),
            team_brief=team_brief,
        )
        return [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user},
        ]

    async def decide(
        self,
        state: GameState,
        log: MessageLog,
        team_brief: TeamBrief | None = None,
        prompt_sink: Callable[[str], Awaitable[None]] | None = None,
    ) -> ParseResult[GameTurn]:
        """Run one ReAct turn: prompt -> model -> validated GameTurn."""
        messages = self.build_messages(state, log, team_brief)
        if prompt_sink is not None:
            await prompt_sink(self.render_prompt(messages))
        last: ParseResult[GameTurn] = ParseResult(ok=False, error="no attempt made")
        turn_schema = GameTurn.model_json_schema()
        for _ in range(self.max_attempts):
            raw = await self.client.chat(
                messages,
                format_schema=turn_schema,
                temperature=self.temperature,
            )
            result = parse_and_validate(raw, GameTurn)
            if result.ok:
                if result.value is not None:
                    self._own_actions.append(action_signature(result.value.action))
                return result
            last = result
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": result.repair_instruction
                    or "Return one valid JSON object only.",
                }
            )
        return last
