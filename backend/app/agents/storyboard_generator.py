"""
StoryboardGenerator — creates a Storyboard from any GameSetting.

Called once before a world is played for the first time. Output is saved as
world_XXX_storyboard.json alongside the world file and reused on all subsequent
runs of that world.

Uses qwen2.5:14b (or STORYBOARD_MODEL env var) for higher quality creative
output than the runtime narrator. Can run slowly — it is an offline step.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from app.agents.storyboard import Storyboard
from app.llm.ollama_client import OllamaClient
from app.schemas.game_setting import GameSetting

STORYBOARD_MODEL = os.environ.get("STORYBOARD_MODEL", "qwen2.5:14b")
STORYBOARD_TEMPERATURE = float(os.environ.get("STORYBOARD_TEMPERATURE", "0.85"))


# ── System prompt ──────────────────────────────────────────────────────────── #

_SYSTEM_PROMPT = """\
You are a STORY ARCHITECT for an interactive escape room game.
Your job is to create the complete HUMAN NARRATIVE LAYER on top of a mechanical puzzle world.

You receive WORLD_DATA describing rooms, objects, locks, and player personas.
You output a single JSON document that maps the mechanical world to a rich human story.

This must work for ANY genre the WORLD_DATA implies:
- Murder mystery in a manor → victim, killer, suspects
- Sci-fi orbital station → system failure, saboteur, crew dynamics
- Asylum horror → institutional abuse, patient records, hidden doctor
- Nautical curse → drowned crew, sea legend, cursed object
- Heist → stolen asset, inside man, escape window

Read WORLD_DATA.scenario and WORLD_DATA.objective to infer the genre.
Then generate ALL sections below appropriate to that genre.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT — output ONLY raw JSON, no markdown, no explanation
Generate sections IN ORDER — the most critical sections come first.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{
  "solution": {
    "question": "The exact question shown to the human player: 'Who murdered X?' / 'Which system failed?' / etc.",
    "answer": "The canonical correct answer — a name, object, or location.",
    "answer_aliases": ["alternative", "phrasings", "accepted"],
    "answer_type": "one of: person / object / location / code",
    "motive": "2 sentences. Full explanation shown only AFTER win.",
    "proof_object": "The object_id from WORLD_DATA.plot_objects that is the key evidence.",
    "proof_sentence": "1 sentence. What that evidence specifically proves.",
    "hint_1": "Vague hint after the human's FIRST wrong guess — directional, not spoiling.",
    "hint_2": "More specific hint after the SECOND wrong guess — still no direct spoiler.",
    "victory_narration_hook": "1 sentence for the victory narration. Names the answer and proof.",
    "defeat_narration_hook": "1 sentence for the defeat narration when wrong deduction exhausted."
  },

  "plot": {
    "victim": "1 sentence. Who was harmed/lost, when, and how — with a specific name.",
    "threat": "1 sentence. The antagonist or danger — CONCRETE, not 'the unknown'.",
    "stakes": "1 sentence. What happens if the team fails.",
    "timeline": "2 sentences. Key events BEFORE the players arrived.",
    "tension_hint": "1 sentence. Vague hint at the hidden motive — not enough to spoil.",
    "atmosphere": "2 sentences. Specific sensory details — smell, sound, light, temperature. NO 'eerie', 'chilling', 'whispers'.",
    "protagonist_context": "1 sentence. Why these specific people are in this exact place."
  },

  "adapted_personas": {
    "<character name from WORLD_DATA.players>": {
      "world_role": "2 sentences. Reframe this character's role for the world's genre. Strip genre contamination — a Systems Operator in a manor does NOT think in power grids.",
      "voice": "1 sentence. Distinct speaking style — must differ from other characters.",
      "vocabulary": ["phrase 1", "phrase 2", "phrase 3"]
    }
  },

  "discovery_beats": {
    "<object_id with clue_type='key'>": "1 sentence. What finding this physical item reveals — specific and physical.",
    "<object_id with clue_type='story_clue'>": "1 sentence. MUST name the killer/answer explicitly. This is the moment the human reader learns who is responsible. Example: 'The name scratched into the lining reads Blackwood — the same name on the guest register the night of the murder.'"
  },

  "conversation_seeds": {
    "<character name from WORLD_DATA.players>": [
      "Seed 1: a specific observation about the evidence — MAY name the killer if this character has seen a clue pointing to them.",
      "Seed 2: a different angle — motive, alibi gap, or physical evidence. At least one seed per character should mention a concrete name or fact."
    ]
  },

  "ending_guidance": {
    "won": "2 sentences. Emotional close for victory. Specific to this world.",
    "lost": "2 sentences. Emotional close for failure. Specific to this world.",
    "lost_by_wrong_deduction": "1 sentence. The killer escaped because the wrong name was called."
  },

  "room_stories": {
    "<room_id from WORLD_DATA.rooms>": "1 sentence. The narrative role of this room."
  }
}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRICT ANTI-HALLUCINATION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. room_stories keys MUST be room ids from WORLD_DATA.rooms — no others.
2. discovery_beats keys MUST be object ids from WORLD_DATA.plot_objects — no others.
3. adapted_personas keys MUST be character names from WORLD_DATA.players — no others.
4. conversation_seeds keys MUST be character names from WORLD_DATA.players — no others.
5. The solution.answer MUST NOT be the name of any character in WORLD_DATA.players.
6. Do NOT reveal mechanical lock codes (numbers, sequences) in any narrative field.
   DO name the killer/answer in discovery_beats for clue_type='story_clue' objects
   and in conversation_seeds — this is how the human player learns the answer.
7. Do NOT invent new rooms, objects, or characters not in WORLD_DATA.
8. Each vocabulary list must contain exactly 3 short phrases — no full sentences.
9. Generate sections in the order given — solution FIRST, room_stories LAST.
"""


# ── User prompt ────────────────────────────────────────────────────────────── #

def _build_user_prompt(setting: GameSetting, world_id: str) -> str:
    """Build the prompt from any GameSetting."""
    obj_by_id = {o.id: o for o in setting.objects}

    # Collect IDs that are referenced as tools/codes by other objects (the "key" side).
    referenced_as_tool: set[str] = set()
    referenced_as_code_source: set[str] = set()
    for obj in setting.objects:
        if obj.requires_tool:
            referenced_as_tool.add(obj.requires_tool)
        if obj.requires_code:
            producer = next(
                (o.id for o in setting.objects if o.contains_info == obj.requires_code),
                None,
            )
            if producer:
                referenced_as_code_source.add(producer)

    # Include both "lock" side (has requires_* or connects_to) AND
    # "key" side (is referenced as a required tool or code source).
    def is_plot_critical(o) -> bool:
        if o.id.startswith(("scenic_", "gate_", "filler_")):
            return False
        return bool(
            o.requires_tool
            or o.requires_code
            or o.contains_info
            or o.connects_to
            or o.id in referenced_as_tool
            or o.id in referenced_as_code_source
        )

    def _clue_type(o) -> str:
        if o.id in referenced_as_tool:
            return "key"          # physical item that unlocks something
        if o.id in referenced_as_code_source:
            return "code"         # contains a mechanical code required by a lock
        if o.contains_info:
            # If the info token contains digits it's a numeric combination code
            # (e.g. "mirror_code_124", "0102") — don't reveal the value.
            # If it's a descriptive token (e.g. "murderer_surname") it's a
            # narrative clue that SHOULD name the answer.
            if re.search(r"\d", o.contains_info):
                return "code"
            return "story_clue"
        if o.requires_tool or o.requires_code or o.connects_to:
            return "lock"
        return "other"

    plot_objects = [
        {
            "id": o.id,
            "location": o.location,
            "description": o.description,
            "clue_type": _clue_type(o),
            "contains_info_token": o.contains_info,
        }
        for o in setting.objects
        if is_plot_critical(o)
    ]

    # Compact dependency graph: "key X opens lock Y".
    relationships = []
    for obj in setting.objects:
        if obj.requires_tool and obj.requires_tool in obj_by_id:
            key = obj_by_id[obj.requires_tool]
            relationships.append(f'"{key.id}" ({key.description[:60]}) → unlocks "{obj.id}"')
        if obj.requires_code:
            producer = next(
                (o for o in setting.objects if o.contains_info == obj.requires_code),
                None,
            )
            if producer:
                relationships.append(f'"{producer.id}" (contains code) → unlocks "{obj.id}"')

    world_data = {
        "world_id": world_id,
        "scenario": setting.scenario,
        "objective": setting.objective,
        "rooms": setting.rooms,
        "players": [
            {
                "name": p.name,
                "role": p.role,
                "skills": list(p.skills)[:3],
                "backstory": p.backstory[:120] if p.backstory else "",
            }
            for p in (setting.players or [])
        ],
        "plot_objects": plot_objects[:12],  # cap to avoid token overflow
        "unlock_chain": relationships[:10],
    }

    return (
        f"Generate the complete narrative storyboard for this escape room world.\n\n"
        f"WORLD_DATA:\n{json.dumps(world_data, indent=2)}\n\n"
        "IMPORTANT — discovery_beats rules:\n"
        "- clue_type='key': write a beat describing finding the physical item.\n"
        "- clue_type='story_clue': write a beat that NAMES the story answer "
        "(killer, saboteur, etc.) from your solution section. This is the moment "
        "the human player learns WHO is responsible. Be explicit — say the name.\n"
        "- clue_type='code': write flavor only — do NOT reveal the code value.\n"
        "- clue_type='lock': skip — only write beats for key/story_clue objects.\n"
        "Generate sections IN ORDER: solution first, room_stories last.\n"
        "conversation_seeds: exactly 2 items per character — at least one seed "
        "per character must mention the killer's name or a direct clue to their identity.\n"
        "Output raw JSON only. Follow ALL STRICT ANTI-HALLUCINATION RULES."
    )


# ── Generator ──────────────────────────────────────────────────────────────── #

class StoryboardGenerator:
    """Generates a Storyboard from any GameSetting via one LLM call.

    Use qwen2.5:14b for best results. Degrades gracefully to an empty
    Storyboard on any error so the game can always proceed.
    """

    def __init__(
        self,
        client: OllamaClient,
        temperature: float = STORYBOARD_TEMPERATURE,
    ) -> None:
        self.client = client
        self.temperature = temperature

    async def generate(self, setting: GameSetting, *, world_id: str = "") -> Storyboard:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(setting, world_id)},
        ]
        try:
            raw = await self.client.chat(
                messages,
                json_mode=True,
                temperature=self.temperature,
            )
        except Exception as exc:
            print(f"[StoryboardGenerator] LLM call failed: {exc}")
            return Storyboard(world_id=world_id)

        if not raw:
            return Storyboard(world_id=world_id)

        # Strip markdown fences if the model wraps output despite instructions.
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.splitlines()[:-1])

        try:
            data = json.loads(cleaned)
        except Exception as exc:
            print(f"[StoryboardGenerator] JSON parse failed: {exc}")
            return Storyboard(world_id=world_id)

        if not isinstance(data, dict):
            return Storyboard(world_id=world_id)

        storyboard = Storyboard.from_dict(data)
        storyboard.world_id = world_id
        storyboard.generated_at = datetime.now(timezone.utc).isoformat()
        return storyboard


def make_storyboard_client(base_url: str | None = None) -> OllamaClient:
    """Construct the Ollama client configured for storyboard generation."""
    from app.llm.ollama_client import DEFAULT_OLLAMA_URL
    return OllamaClient(
        model=STORYBOARD_MODEL,
        base_url=base_url or DEFAULT_OLLAMA_URL,
        timeout=300.0,  # 5-minute timeout — storyboard generation can be slow
    )
