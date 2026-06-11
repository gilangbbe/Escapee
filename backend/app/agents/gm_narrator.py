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
    NARRATOR_EVENT_SYSTEM_PROMPT,
    NARRATOR_SYSTEM_PROMPT,
    SystemEventKind,
    WorldSnapshot,
    build_discovery_prompt,
    build_ending_user_prompt,
    build_opening_user_prompt,
    build_room_entry_prompt,
    build_system_event_prompt,
    build_turn_user_prompt,
)
from app.agents.storyboard import Storyboard
from app.llm.ollama_client import OllamaClient
from app.schemas.game_setting import GameSetting


@dataclass
class GameMasterNarrator:
    """Narrates the live game as prose, with a rolling memory for continuity."""

    client: OllamaClient
    temperature: float = 0.8
    recent_window: int = 6
    storyboard: Storyboard = field(default_factory=Storyboard)

    _recent: list[str] = field(default_factory=list, init=False)
    _used_seeds: set[str] = field(default_factory=set, init=False)
    _proof_revealed: bool = field(default=False, init=False)

    def reveal_proof(self) -> None:
        """Flip narrator into revelation mode.

        Called the moment the proof object is discovered. From this point on,
        the narrator is allowed to name the killer. Before this call, mystery
        mode is active and the killer's name is suppressed from all dialogue.
        """
        self._proof_revealed = True

    def _normalize_dialogue_line(self, text: str) -> str:
        """Extract message text from model output in `Name: "message"` format."""
        import re
        line = " ".join((text or "").strip().splitlines()).strip()
        if not line:
            return "..."
        # Strip CJK and other non-Latin script characters (model switching to Chinese).
        line = re.sub(r"[⺀-鿿豈-﫿︰-﹏＀-￯]+", "", line).strip()
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

        # Truncate at embedded character-name continuation the model appended.
        # Pattern: quote/spaces then CapWord CapWord: (a second speaker prefix).
        # e.g. 'message."   Alex Quinn: "second message' → keep only first part.
        line = re.split(r'["\s]{2,}[A-Z][a-z]+ [A-Z][a-z]+\s*:', line)[0]
        line = line.rstrip('"').strip()

        # Hard strip forbidden dash characters regardless of model compliance.
        line = line.replace("—", ",").replace("–", ",").replace("--", ",")

        return line or "..."

    def _clean_prose(self, text: str) -> str:
        """Clean raw prose output (narrator beats) — strip role prefixes and dashes."""
        import re as _re
        line = " ".join((text or "").strip().splitlines()).strip()
        if not line:
            return "..."
        line = _re.sub(r"[⺀-鿿豈-﫿︰-﹏＀-￯]+", "", line).strip()
        if not line:
            return "..."
        for prefix in ("assistant:", "narrator:", "prose:"):
            if line.lower().startswith(prefix):
                line = line[len(prefix):].strip()
                break
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
        """Store the character's speech as a chat line so STORY_SO_FAR reads as a conversation."""
        record = f'{actor_name}: "{speech}"'
        self._recent.append(record)
        if len(self._recent) > self.recent_window:
            self._recent = self._recent[-self.recent_window:]

    def _lore_excerpt(self, *, actor_name: str, room_id: str, object_id: str = "") -> str:
        return self.storyboard.lore_excerpt(
            actor_name=actor_name, room_id=room_id, object_id=object_id
        )

    def connection_lore_for(self, object_id: str) -> str:
        """Return the pre-written story sentence for a specific object, or empty string."""
        return self.storyboard.discovery_beat(object_id)

    async def narrate_opening(self, setting: GameSetting) -> str:
        text = await self._say(
            build_opening_user_prompt(setting, self.storyboard),
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
        actor_gender: str = "",
        action_text: str,
        speech: str | None,
        outcome: str,
        success: bool,
        status: ActionExecutionStatus,
        room_id: str = "",
        object_id: str = "",
        world_snapshot: WorldSnapshot | None = None,
    ) -> str:
        lore_excerpt = self._lore_excerpt(
            actor_name=actor_name, room_id=room_id, object_id=object_id
        )
        persona = self.storyboard.persona_for(actor_name)
        seed = self.storyboard.pop_seed(actor_name, self._used_seeds)
        if seed:
            self._used_seeds.add(seed)

        # Prefer storyboard.mystery (authoritative generated source); fall back to solution.answer.
        killer_name = (
            (self.storyboard.mystery.killer_name or self.storyboard.solution.answer)
            if self._proof_revealed else ""
        )
        line = await self._say(
            build_turn_user_prompt(
                scenario=scenario,
                objective=objective,
                turn=turn,
                actor_name=actor_name,
                actor_role=actor_role,
                actor_skills=actor_skills,
                actor_backstory=actor_backstory,
                actor_gender=actor_gender,
                action_text=action_text,
                speech=speech,
                outcome=outcome,
                success=success,
                status=status,
                recent_story=list(self._recent),
                lore_excerpt=lore_excerpt,
                world_snapshot=world_snapshot,
                adapted_world_role=persona.world_role if persona else "",
                adapted_vocabulary=persona.vocabulary if persona else None,
                conversation_seed=seed or "",
                proof_revealed=self._proof_revealed,
                killer_name=killer_name,
                suspects_context=self.storyboard.suspects_context(),
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

    async def narrate_room_entry(
        self,
        *,
        actor_name: str,
        room_id: str,
        scenario: str,
    ) -> str:
        """One-time atmospheric beat the first time a character enters a room."""
        text = await self._say(
            build_room_entry_prompt(
                actor_name=actor_name,
                room_id=room_id,
                room_story=self.storyboard.room_story(room_id),
                scenario=scenario,
            ),
            system=NARRATOR_EVENT_SYSTEM_PROMPT,
            fallback="",
        )
        if text:
            self._remember(text)
        return text

    async def narrate_discovery(
        self,
        *,
        actor_name: str,
        item_id: str,
        item_description: str,
        unlocks_description: str,
        connection_lore: str,
        scenario: str,
    ) -> str:
        """One-time discovery beat: connects a found item to what it unlocks.

        Fires the first time a plot-critical object is touched (taken or inspected).
        If the storyboard has a pre-written beat for this object, that sentence is
        emitted directly — no LLM call. Falls back to LLM generation only when the
        storyboard has no entry for this object.
        """
        pre_written = self.storyboard.discovery_beat(item_id)
        if pre_written:
            self._remember(pre_written)
            return pre_written

        # Fallback: generate at runtime using LLM.
        text = await self._say(
            build_discovery_prompt(
                actor_name=actor_name,
                item_id=item_id,
                item_description=item_description,
                unlocks_description=unlocks_description,
                connection_lore=connection_lore,
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

    async def narrate_ending(
        self, setting: GameSetting, won: bool, *, wrong_deduction: bool = False
    ) -> str:
        fallback = (
            "The crew breaks free into the light." if won
            else "Time runs out. The truth stays buried."
        )
        ending_guidance = self.storyboard.ending_text(won, wrong_deduction=wrong_deduction)
        text = await self._say(
            build_ending_user_prompt(setting, won, ending_guidance=ending_guidance),
            system=NARRATOR_EVENT_SYSTEM_PROMPT,
            fallback=fallback,
        )
        self._remember(text)
        return text
