"""
WorldPlot — pre-game narrative context generator.

Before the first turn, the PlotGenerator reads any GameSetting JSON cold and
produces a WorldPlot: a structured story document that maps the mechanical
world (rooms, objects, locks) to human narrative (characters, stakes, history,
what each object means to the story).

This runs once at game start, works for any world theme (sci-fi, mystery,
heist, horror, supernatural), and its output is used by the GameMasterNarrator
for every beat: dialogue, narration cards, discovery beats, room entries.

The generator is intentionally separate from the Narrator so it can be cached,
logged, or regenerated without affecting game mechanics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.llm.ollama_client import OllamaClient
from app.schemas.game_setting import GameSetting


# ── Schema ────────────────────────────────────────────────────────────────── #

@dataclass
class WorldPlot:
    """Complete narrative context for one game session.

    All fields default to empty so a failed generation degrades gracefully.
    """
    # Who these people are and why they're here (answers the "so what" question).
    protagonist_context: str = ""
    # The threat, antagonist, or driving danger — theme-appropriate.
    threat: str = ""
    # The victim, if applicable (mystery/horror themes).
    victim: str = ""
    # What happens concretely if the team fails.
    stakes: str = ""
    # Key events that happened BEFORE the players arrived.
    timeline: str = ""
    # Vague hint at motive or hidden cause (no spoilers).
    tension_hint: str = ""
    # Per-room: the story role of this room in the drama.
    room_stories: dict[str, str] = field(default_factory=dict)
    # Per-object: what this object means to the story (beyond mechanics).
    object_stories: dict[str, str] = field(default_factory=dict)
    # Per-character: a distinct voice/personality line for the narrator to use.
    character_voices: dict[str, str] = field(default_factory=dict)
    # The overall tone/atmosphere of this world.
    atmosphere: str = ""

    def is_empty(self) -> bool:
        return not any([
            self.protagonist_context, self.threat, self.victim,
            self.stakes, self.timeline, self.room_stories,
        ])

    def lore_excerpt(
        self,
        *,
        actor_name: str = "",
        room_id: str = "",
        object_id: str = "",
    ) -> str:
        """Return the most relevant plot fields for a single narrator beat."""
        parts: list[str] = []
        if self.victim:
            parts.append(f"THE VICTIM: {self.victim}")
        if self.tension_hint:
            parts.append(f"TENSION: {self.tension_hint}")
        if self.atmosphere:
            parts.append(f"ATMOSPHERE: {self.atmosphere}")
        if actor_name and actor_name in self.character_voices:
            parts.append(f"VOICE ({actor_name}): {self.character_voices[actor_name]}")
        if room_id and room_id in self.room_stories:
            parts.append(f"THIS ROOM: {self.room_stories[room_id]}")
        if object_id and object_id in self.object_stories:
            parts.append(f"THIS OBJECT: {self.object_stories[object_id]}")
        return "\n".join(parts)

    def object_story(self, object_id: str) -> str:
        return self.object_stories.get(object_id, "")

    def room_story(self, room_id: str) -> str:
        return self.room_stories.get(room_id, "")

    def character_voice(self, name: str) -> str:
        return self.character_voices.get(name, "")


# ── Prompts ───────────────────────────────────────────────────────────────── #

_PLOT_SYSTEM_PROMPT = """\
You are a STORY WRITER for an interactive escape room game.
Your job is to create the HUMAN NARRATIVE LAYER on top of a mechanical puzzle world.

You will receive a WORLD_DATA JSON describing rooms, objects, and how they connect.
Your output tells the narrator WHO these people are, WHAT happened, WHY it matters,
and WHAT each object and room means to the story.

This must work for ANY genre: mystery, sci-fi, horror, heist, supernatural, etc.
Read the scenario and infer the genre — then write accordingly.

OUTPUT CONTRACT:
Output a single JSON object with exactly these keys. No markdown. No extra text.

"protagonist_context": 1-2 sentences. Who are these specific people and WHY are they
  in this exact place? Not generic survivors — what is their connection to this world?
  Sci-fi example: "The repair droids were assigned to Aethelgard Station three weeks
  before the accident. They alone know where the backup reactor keys are hidden."
  Mystery example: "The guests were summoned by an unsigned letter promising evidence
  of a family fortune. Now one of them is dead and the roads are flooded."

"threat": 1 sentence. The antagonist, danger, or driving force — concrete and specific.
  NOT "the darkness" or "the unknown". Something with weight.

"victim": 1 sentence. If there is a victim (murdered person, missing person, casualty)
  give them a name and say specifically what happened to them. If no victim, leave empty.
  Example: "Eliza Harwick, 34, was found face-down in the study at midnight, the ink
  on her letter to the police still wet."

"stakes": 1 sentence. What happens specifically if the team fails or runs out of time?

"timeline": 2-3 sentences. Key events that happened BEFORE the players arrived.
  Give the story a history. What sequence of decisions led to this moment?

"tension_hint": 1 sentence. A vague hint at the motive or hidden cause — enough to
  make the story feel connected, not enough to spoil the mystery.

"atmosphere": 2 sentences. Specific sensory details of this world — smell, sound,
  light, temperature. NO generic words: no "eerie", "chilling", "unsettling".
  BAD: "The air hangs heavy with an eerie, unsettling presence."
  GOOD: "The floorboards are still warm from a fire that went out hours ago.
  Every door in the manor is slightly ajar, as if someone left in a hurry."

"room_stories": object mapping each room_id to 1 sentence describing this room's
  ROLE in the story — what happened here, why the team should care.
  Example: {"library": "This is where Eliza was working the night she died."}

"object_stories": object mapping each PLOT-CRITICAL object_id to 1 sentence
  explaining what this object means to the story — beyond its mechanical function.
  Example: {"bloodstained_letter": "Eliza wrote this letter naming her killer,
  but sealed it inside the drawer before she could send it."}
  Only include objects that appear in WORLD_DATA.plot_objects — do not invent others.

"character_voices": object mapping each character name to 1 sentence of DISTINCT
  voice/personality. Each character MUST sound different from the others.
  Base it on their role and skills. Examples:
  - Field Analyst: "Speaks in precise observations, never speculation — 'the mark
    on the frame is made by a ring, not a key'"
  - Systems Operator: "Thinks in patterns and sequences — catches what others miss"
  - Scout: "Moves and thinks in instinct — 'something's wrong here, I can feel it'"

STRICT RULES:
1. object_stories keys MUST be from WORLD_DATA.plot_objects only.
2. room_stories keys MUST be from WORLD_DATA.rooms only.
3. character_voices keys MUST be from WORLD_DATA.players only.
4. Do NOT invent new characters, rooms, or objects.
5. Do NOT reveal puzzle codes or solutions.
6. The victim name you invent must NOT match any character in WORLD_DATA.players.
"""


def _build_plot_prompt(setting: GameSetting) -> str:
    """Build the plot generation prompt from any GameSetting."""
    plot_objects = [
        {
            "id": o.id,
            "location": o.location,
            "description": o.description,
            "requires_tool": o.requires_tool,
            "requires_code": o.requires_code is not None,
            "contains_info": o.contains_info,
            "connects_to": o.connects_to,
        }
        for o in setting.objects
        if (o.requires_tool or o.requires_code or o.contains_info or o.connects_to)
        and not o.id.startswith("scenic_")
        and not o.id.startswith("gate_")
        and not o.id.startswith("filler_")
    ]

    world_data = {
        "scenario": setting.scenario,
        "objective": setting.objective,
        "rooms": setting.rooms,
        "players": [
            {
                "name": p.name,
                "role": p.role,
                "skills": list(p.skills),
                "backstory": p.backstory,
            }
            for p in (setting.players or [])
        ],
        "plot_objects": plot_objects,
    }

    return (
        "Generate the narrative layer for this escape room world.\n\n"
        f"WORLD_DATA:\n{json.dumps(world_data, indent=2)}\n\n"
        "Output raw JSON only. Follow all STRICT RULES."
    )


# ── Generator ─────────────────────────────────────────────────────────────── #

class PlotGenerator:
    """Generates a WorldPlot for any GameSetting before the game starts."""

    def __init__(self, client: OllamaClient, temperature: float = 0.75) -> None:
        self.client = client
        self.temperature = temperature

    async def generate(self, setting: GameSetting) -> WorldPlot:
        """Call the LLM and parse the result into a WorldPlot.

        Always returns a WorldPlot — degrades gracefully to an empty one on
        any error so the game can proceed without narrative context.
        """
        messages = [
            {"role": "system", "content": _PLOT_SYSTEM_PROMPT},
            {"role": "user", "content": _build_plot_prompt(setting)},
        ]
        try:
            raw = await self.client.chat(messages, temperature=self.temperature)
        except Exception:
            return WorldPlot()

        if not raw:
            return WorldPlot()

        # Strip markdown fences if the model wraps output despite instructions.
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.splitlines()[:-1])

        try:
            data = json.loads(cleaned)
        except Exception:
            return WorldPlot()

        if not isinstance(data, dict):
            return WorldPlot()

        return WorldPlot(
            protagonist_context=data.get("protagonist_context", ""),
            threat=data.get("threat", ""),
            victim=data.get("victim", ""),
            stakes=data.get("stakes", ""),
            timeline=data.get("timeline", ""),
            tension_hint=data.get("tension_hint", ""),
            atmosphere=data.get("atmosphere", ""),
            room_stories=data.get("room_stories", {}),
            object_stories=data.get("object_stories", {}),
            character_voices=data.get("character_voices", {}),
        )
