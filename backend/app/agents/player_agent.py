"""
PlayerAgent — wires persona + context + LLM + JSON repair into one ReAct turn.

`decide()` assembles the per-turn prompt from authoritative state, calls the
local 7B model via Ollama (json mode), and parses/repairs the output into a
validated `PlayerTurn`. The orchestrator (Phase 4) takes the resulting
`PlayerAction`, feeds it to the WorldSimulator, and writes the Observation back
into the MessageLog.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.player_prompt import (
    build_player_system_prompt,
    build_player_user_prompt,
    primary_objective,
)
from app.agents.player_turn import PlayerTurn
from app.context.builder import (
    build_state_view,
    render_recent_events,
    summarize_events,
)
from app.context.channels import MessageLog
from app.engine.state import WorldState
from app.llm.json_repair import ParseResult, parse_and_validate
from app.llm.ollama_client import OllamaClient
from app.schemas.blueprint import PlayerPersona


@dataclass
class PlayerAgent:
    """A single player's persona, model binding, and rolling summary memory."""

    persona: PlayerPersona
    client: OllamaClient
    recent_window: int = 8
    max_attempts: int = 3
    temperature: float = 0.7

    # Per-player compressed memory of older events.
    rolling_summary: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self._system_prompt = build_player_system_prompt(self.persona)

    def build_messages(
        self, state: WorldState, log: MessageLog
    ) -> list[dict[str, str]]:
        """Assemble the full prompt for this turn from authoritative state."""
        view = build_state_view(state, self.persona.id)
        recent_text, older = render_recent_events(
            log, self.persona.id, self.recent_window
        )
        # Fold anything beyond the recent window into the rolling summary.
        if older:
            self.rolling_summary = summarize_events(self.rolling_summary, older)

        user = build_player_user_prompt(
            objective=primary_objective(state.blueprint),
            state_view_text=view.render(),
            private_clues=log.private_clues.get(self.persona.id, []),
            rolling_summary=self.rolling_summary,
            recent_events_text=recent_text,
        )
        return [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user},
        ]

    async def decide(
        self, state: WorldState, log: MessageLog
    ) -> ParseResult[PlayerTurn]:
        """Run one ReAct turn: prompt -> model -> validated PlayerTurn."""
        messages = self.build_messages(state, log)
        last: ParseResult[PlayerTurn] = ParseResult(
            ok=False, error="no attempt made"
        )
        for _ in range(self.max_attempts):
            raw = await self.client.chat(
                messages, json_mode=True, temperature=self.temperature
            )
            result = parse_and_validate(raw, PlayerTurn)
            if result.ok:
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
