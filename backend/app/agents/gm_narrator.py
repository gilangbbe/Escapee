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

import json
from dataclasses import dataclass, field

from app.agents.gm_narrator_prompt import (
    ActionExecutionStatus,
    LORE_SYSTEM_PROMPT,
    NARRATOR_EVENT_SYSTEM_PROMPT,
    NARRATOR_SYSTEM_PROMPT,
    SystemEventKind,
    WorldSnapshot,
    build_ending_user_prompt,
    build_lore_prompt,
    build_opening_user_prompt,
    build_system_event_prompt,
    build_turn_user_prompt,
    extract_lore_excerpt,
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
    _lore: dict = field(default_factory=dict, init=False)

    def _normalize_dialogue_line(self, text: str) -> str:
        """Extract just the message text from model output.

        Model outputs `Name: "message"` — we strip the name prefix and quotes
        so the caller gets bare message text. actor_id on the event carries
        the speaker identity; we don't need it duplicated in the text.
        """
        line = " ".join((text or "").strip().splitlines()).strip()
        if not line:
            return "..."

        # Strip accidental role prefixes from weaker local models.
        for prefix in ("assistant:", "narrator:", "dialogue:"):
            if line.lower().startswith(prefix):
                line = line[len(prefix):].strip()
                break

        # Strip `Name: "message"` → extract just the message.
        if ":" in line:
            parts = line.split(":", 1)
            candidate = parts[1].strip().strip('"').strip()
            if candidate:
                line = candidate

        # Strip surrounding quotes if present.
        if line.startswith('"') and line.endswith('"') and len(line) > 1:
            line = line[1:-1].strip()

        # Hard strip forbidden dash characters regardless of model compliance.
        line = line.replace("—", ",").replace("–", ",").replace("--", ",")

        return line or "..."

    async def _say(self, user_prompt: str, *, system: str, fallback: str) -> str:
        messages = [
            {"role": "system", "content": system},
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
        """Store a bare prose line (for opening, milestone, system events)."""
        self._recent.append(line)
        if len(self._recent) > self.recent_window:
            self._recent = self._recent[-self.recent_window:]

    def remember_turn(
        self,
        *,
        actor_name: str,
        action_text: str,
        success: bool,
        speech: str,
        turn_no: int,
    ) -> None:
        """Store a structured turn record so narrator can see attribution + outcome + speech."""
        status = "OK" if success else "FAIL"
        record = f"[Turn {turn_no}] {actor_name} → {action_text} → {status}: \"{speech}\""
        self._recent.append(record)
        if len(self._recent) > self.recent_window:
            self._recent = self._recent[-self.recent_window:]

    def _lore_excerpt(self, *, actor_name: str, room_id: str) -> str:
        if not self._lore:
            return ""
        return extract_lore_excerpt(self._lore, actor_name=actor_name, room_id=room_id)

    async def generate_lore(self, setting: GameSetting) -> None:
        """Generate and store a one-time backstory/lore doc from the world JSON.

        Called once before the game starts. Output is stored in self._lore and
        used as tone/voice context for narrate_turn(). Fails silently — if the
        model errors or returns invalid JSON, lore stays empty and the game
        continues without it.
        """
        raw = await self._say(
            build_lore_prompt(setting),
            system=LORE_SYSTEM_PROMPT,
            fallback="",
        )
        if not raw:
            return
        # Strip markdown code fences if model wraps output despite instructions.
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.splitlines()[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.splitlines()[:-1])
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                self._lore = parsed
        except Exception:
            pass  # Lore is best-effort; game continues without it.

    async def narrate_opening(self, setting: GameSetting) -> str:
        text = await self._say(
            build_opening_user_prompt(setting),
            system=NARRATOR_EVENT_SYSTEM_PROMPT,
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
        room_id: str = "",
        world_snapshot: WorldSnapshot | None = None,
    ) -> str:
        lore_excerpt = self._lore_excerpt(actor_name=actor_name, room_id=room_id)
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
                lore_excerpt=lore_excerpt,
                world_snapshot=world_snapshot,
            ),
            system=NARRATOR_SYSTEM_PROMPT,
            fallback=f'{actor_name}: "{outcome}"',
        )
        normalized = self._normalize_dialogue_line(line)
        self.remember_turn(
            actor_name=actor_name,
            action_text=action_text,
            success=success,
            speech=normalized,
            turn_no=turn,
        )
        return normalized

    async def narrate_system_event(
        self,
        *,
        event_kind: SystemEventKind,
        actor_name: str,
        detail: str,
        scenario: str,
    ) -> str:
        """Generate atmospheric narrator prose for internal system events (loop, override, stuck)."""
        text = await self._say(
            build_system_event_prompt(
                event_kind=event_kind,
                actor_name=actor_name,
                detail=detail,
                scenario=scenario,
            ),
            system=NARRATOR_EVENT_SYSTEM_PROMPT,
            fallback="",
        )
        if text:
            self._remember(text)
        return text

    async def narrate_milestone(self, *, milestone: str, setting: GameSetting) -> str:
        """Generate a short atmospheric narrator beat for a progress milestone."""
        prompt = (
            f"SCENARIO: {setting.scenario}\n"
            f"MILESTONE JUST ACHIEVED: {milestone}\n\n"
            "Write 1-2 sentences of tense, atmospheric third-person narration "
            "reacting to this breakthrough. Present tense. No character names. "
            "No invented facts. No em-dashes. Focus on the mood shift."
        )
        text = await self._say(prompt, system=NARRATOR_EVENT_SYSTEM_PROMPT, fallback="")
        if text:
            self._remember(text)
        return text

    async def narrate_ending(self, setting: GameSetting, won: bool) -> str:
        fallback = (
            "The crew breaks free into the dark." if won
            else "Time runs out, and the station keeps its prisoners."
        )
        text = await self._say(
            build_ending_user_prompt(setting, won),
            system=NARRATOR_EVENT_SYSTEM_PROMPT,
            fallback=fallback,
        )
        self._remember(text)
        return text
