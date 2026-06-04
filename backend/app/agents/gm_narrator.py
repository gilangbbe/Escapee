"""
GameMasterNarrator — the storytelling LLM.

Wraps a local Ollama model and converts the structured game (actions + the
simulator's authoritative outcomes) into a flowing, present-tense narrative for
the human audience. It maintains its own short rolling memory of recent prose
for continuity.

This is a PRESENTATION layer: its output is streamed to observers only and is
never added to the shared `MessageLog`, so it can never leak information between
player agents nor influence ground truth. If the model errors, each method
degrades gracefully to a deterministic fallback so the game keeps flowing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.gm_narrator_prompt import (
    ActionExecutionStatus,
    NARRATOR_SYSTEM_PROMPT,
    build_ending_user_prompt,
    build_opening_user_prompt,
    build_turn_user_prompt,
)
from app.llm.ollama_client import OllamaClient
from app.schemas.game_setting import GameSetting


@dataclass
class GameMasterNarrator:
    """Narrates the live game as prose, with a rolling memory for continuity."""

    client: OllamaClient
    temperature: float = 0.8
    recent_window: int = 6

    _recent: list[str] = field(default_factory=list, init=False)

    def _normalize_dialogue_line(self, text: str, *, actor_name: str) -> str:
        """Ensure output is exactly one chat line: Name: "message"."""
        line = " ".join((text or "").strip().splitlines()).strip()
        if not line:
            return f'{actor_name}: "..."'

        if ":" in line and "\"" in line:
            return line

        # Strip accidental role prefixes from weaker local models.
        for prefix in ("assistant:", "narrator:", "dialogue:"):
            if line.lower().startswith(prefix):
                line = line[len(prefix):].strip()
                break
        if line.startswith('"') and line.endswith('"'):
            return f"{actor_name}: {line}"
        return f'{actor_name}: "{line}"'

    async def _say(self, user_prompt: str, *, fallback: str) -> str:
        messages = [
            {"role": "system", "content": NARRATOR_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        try:
            raw = await self.client.chat(messages, temperature=self.temperature)
        except Exception:
            return fallback
        text = (raw or "").strip()
        if not text:
            return fallback
        return text

    def _remember(self, line: str) -> None:
        self._recent.append(line)
        if len(self._recent) > self.recent_window:
            self._recent = self._recent[-self.recent_window:]

    async def narrate_opening(self, setting: GameSetting) -> str:
        text = await self._say(
            build_opening_user_prompt(setting),
            fallback=setting.scenario,
        )
        self._remember(text)
        return text

    async def narrate_turn(
        self,
        *,
        scenario: str,
        objective: str,
        turn: int,
        actor_name: str,
        actor_role: str,
        actor_skills: list[str],
        actor_backstory: str,
        action_text: str,
        speech: str | None,
        outcome: str,
        success: bool,
        status: ActionExecutionStatus,
    ) -> str:
        line = await self._say(
            build_turn_user_prompt(
                scenario=scenario,
                objective=objective,
                turn=turn,
                actor_name=actor_name,
                actor_role=actor_role,
                actor_skills=actor_skills,
                actor_backstory=actor_backstory,
                action_text=action_text,
                speech=speech,
                outcome=outcome,
                success=success,
                status=status,
                recent_story=list(self._recent),
            ),
            fallback=f'{actor_name}: "{outcome}"',
        )
        normalized = self._normalize_dialogue_line(line, actor_name=actor_name)
        self._remember(normalized)
        return normalized

    async def narrate_ending(self, setting: GameSetting, won: bool) -> str:
        fallback = (
            "The crew breaks free into the dark." if won
            else "Time runs out, and the station keeps its prisoners."
        )
        text = await self._say(
            build_ending_user_prompt(setting, won),
            fallback=fallback,
        )
        self._remember(text)
        return text
