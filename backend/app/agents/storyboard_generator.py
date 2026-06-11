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
  "mystery": {
    "victim": "Full name of the person killed — e.g. 'Dr. Clara Morse was found dead in the library at dawn'.",
    "killer_name": "Full name of the murderer — must match solution.answer exactly. Invent a name specific to THIS world's scenario.",
    "proof_object_id": "COPY the exact `id` string from one WORLD_DATA.plot_objects entry with clue_type='story_clue'. This is the id field — NOT the description. E.g. if the object has id 'curator_s_final_lockbox', write 'curator_s_final_lockbox'.",
    "motive_hint": "1 sentence. The killer's motive — specific but not enough to spoil immediately."
  },

  "solution": {
    "question": "The exact question shown to the human player: 'Who murdered X?' / 'Which system failed?' / etc.",
    "answer": "The canonical correct answer — must equal mystery.killer_name exactly.",
    "answer_aliases": ["alternative", "phrasings", "accepted"],
    "answer_type": "one of: person / object / location / code",
    "motive": "2 sentences. Full explanation shown only AFTER win.",
    "proof_object": "Must equal mystery.proof_object_id exactly — the id string, not the description.",
    "proof_sentence": "1 sentence. What that evidence specifically proves.",
    "hint_1": "Vague hint after the human's FIRST wrong guess — directional, not spoiling.",
    "hint_2": "More specific hint after the SECOND wrong guess — still no direct spoiler.",
    "victory_narration_hook": "1 sentence for the victory narration. Names the answer and proof.",
    "defeat_narration_hook": "1 sentence for the defeat narration when wrong deduction exhausted."
  },

  "suspects": [
    {
      "name": "Full name of a plausible suspect — invent names specific to THIS world's scenario",
      "connection_to_victim": "1 sentence. How this person knew the victim.",
      "apparent_motive": "1 sentence. Why they might have done it — plausible even if they are innocent.",
      "is_killer": true
    },
    {
      "name": "A second plausible suspect — a red herring. Use a DIFFERENT name, not from the examples.",
      "connection_to_victim": "1 sentence.",
      "apparent_motive": "1 sentence. Their motive should seem credible for the first half of the game.",
      "is_killer": false
    }
  ],

  "discovery_beats": {
    "<object_id with clue_type='key'>": "2 sentences. Physical detail + what its presence implies. End on who might have left it or why. Do NOT name any suspect.",
    "<object_id with clue_type='story_clue' NOT proof>": "2 sentences. Name ONE suspect (not the killer) and connect this evidence to them. Use the ACTUAL suspect names from your suspects list above.",
    "<object_id matching mystery.proof_object_id>": "2 sentences. REVELATION — name mystery.killer_name (from your mystery block above). State what this proves. Use the ACTUAL killer name you chose, not an example name."
  },

  "conversation_seeds": {
    "<character name from WORLD_DATA.players>": [
      "Seed 1: Names a suspect by their full name and connects physical evidence to them. Do NOT name the killer as killer.",
      "Seed 2: A different angle — names a different suspect, or probes motive/alibi. Use ACTUAL suspect names from your suspects list."
    ]
  },

  "ending_guidance": {
    "won": "2 sentences. Emotional close for victory. Names the killer (use the name from your mystery block).",
    "lost": "2 sentences. Emotional close for failure. Specific to this world.",
    "lost_by_wrong_deduction": "1 sentence. The killer escaped because the wrong name was called."
  },

  "plot": {
    "victim": "1 sentence. Who was harmed/lost, when, and how — with a specific name.",
    "threat": "1 sentence. The antagonist or danger — CONCRETE, not 'the unknown'.",
    "stakes": "1 sentence. What happens if the team fails. Do NOT name the killer or any suspect.",
    "timeline": "2 sentences. Key events BEFORE the players arrived.",
    "tension_hint": "1 sentence. Vague hint at the hidden motive — not enough to spoil.",
    "atmosphere": "2 sentences. Specific sensory details — smell, sound, light, temperature.",
    "protagonist_context": "1 sentence. Why these specific people are in this exact place."
  },

  "adapted_personas": {
    "<character name from WORLD_DATA.players>": {
      "world_role": "1-2 sentences. HOW this character investigates — their specific lens on clues. NOT their job title. NOT their technical skills. Concretely describe what they notice first and how they interpret it in THIS WORLD'S genre.",
      "voice": "1 sentence. Their distinct speaking style. Must sound different from every other character.",
      "vocabulary": ["5-word-max phrase 1", "5-word-max phrase 2", "5-word-max phrase 3"]
    }
  },

  "discovery_beats": {
    "<object_id with clue_type='key'>": "2 sentences. Describe the physical detail AND what its presence implies about the investigation — who had access, whether it was recently used, what it tells about the timeline. End on an unanswered question: who left this, who needed it. Do NOT name any suspect.",
    "<object_id with clue_type='story_clue' that is NOT the proof_object>": "2 sentences. Name ONE suspect (not the killer) and connect this evidence to their access, opportunity, or motive. GOOD: 'The initials scratched here match Jonathan Hale — he has been in this room, and lied about it.' BAD: 'A slender silver key with initials.' (no suspect named — rejected)",
    "<object_id that matches solution.proof_object>": "2 sentences. THIS IS THE REVELATION MOMENT — name mystery.killer_name explicitly. State exactly what this evidence proves. GOOD: 'The letter is in Isabella Finch's handwriting — she was here the night of the murder.' BAD: 'A yellowed letter with smudged ink.' (killer not named — rejected)"
  },

  "conversation_seeds": {
    "<character name from WORLD_DATA.players>": [
      "Seed 1: a specific observation about physical evidence or timing — what this character notices and what it implies. Do NOT name the killer.",
      "Seed 2: a different investigative angle — motive gap, alibi, or what doesn't add up. Concrete and specific. Do NOT name the killer."
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
SUSPECTS — CRITICAL RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Generate 2 to 3 suspects total.
- Exactly ONE suspect must have "is_killer": true — this person MUST match solution.answer exactly.
- All others have "is_killer": false and are red herrings. Their apparent_motive must be
  GENUINELY PLAUSIBLE for the first half of the game — not obviously wrong.
- Suspects should have different types of motive: one financial, one personal/emotional, one opportunistic.
- Suspects must NOT be any character in WORLD_DATA.players (they are investigators, not suspects).
- Each suspect MUST use exactly these field names: "name", "connection_to_victim", "apparent_motive", "is_killer".
  Do NOT use "relationship_to_victim", "alibi", "motive", or any other names.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DISCOVERY_BEATS — MYSTERY RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Every discovery beat MUST provide investigative value — not just object description.
Each beat MUST do at least ONE of these:
  - Reveal a new fact about the crime
  - Narrow the suspect pool (by naming a suspect and connecting evidence to them)
  - Establish opportunity, motive, or means for someone
  - Contradict what someone implied or stated
  - Connect this clue to the wider picture

BEAT RULES BY CLUE TYPE:
- clue_type='key': Physical detail + investigative implication. Do NOT name any suspect.
  GOOD: "The key is still warm — whoever used it left this room within the last hour."
  BAD:  "A key with floral patterns." ← description only, no investigative value — REJECTED

- clue_type='story_clue' that is NOT proof_object: Name ONE suspect (not the killer).
  Use the ACTUAL suspect name you invented in the suspects list — not a placeholder.
  Connect this evidence to their access, opportunity, or motive. May be a red herring.
  GOOD: "[Suspect name from your list] — the initials match, and they lied about being here."
  BAD:  "A slender key with initials." ← no suspect named — REJECTED

- clue_type='story_clue' that IS proof_object: MUST name mystery.killer_name explicitly.
  Use the ACTUAL killer name you invented in the mystery block — not a placeholder, not an example.
  State exactly what this proves. This is the climax — be definitive.
  GOOD: "[mystery.killer_name]'s handwriting is on this — they were here the night of the murder."
  BAD:  "A yellowed letter with smudged ink." ← killer not named — REJECTED

- clue_type='code': Flavor only — atmosphere, no numbers, no codes.
- clue_type='lock': Skip — write NO beat for lock objects.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ADAPTED_PERSONAS — CRITICAL RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Each character has a mechanical game role (Systems Operator, Field Analyst, Scout, etc.).
You MUST translate this into an investigator archetype that fits the WORLD's genre.
Do NOT copy or paraphrase the original role. Translate it completely.

TRANSLATION FOR COMMON ROLES (apply to whichever genre WORLD_DATA describes):

  MANOR MYSTERY / CRIME / GOTHIC:
  - Systems Operator → reads the SEQUENCE of events: who was in which room, when, and whether timing was deliberate
    world_role example: "Riley maps the sequence of events like a crime scene — who was in each room, in what order, and why the timing matters."
    vocabulary example: ["the timing is off", "someone planned this", "who had access"]
  - Field Analyst → reads PHYSICAL EVIDENCE: what objects reveal about intent and action
    world_role example: "Alex reads physical evidence for what it implies about intent — the angle of a broken object tells him whether it fell or was placed."
    vocabulary example: ["the evidence contradicts that", "this wasn't an accident", "look at what's missing"]
  - Scout → reads the ENVIRONMENT: disturbances, traces, what doesn't fit
    world_role example: "Mara reads spaces before touching anything — she spots what's been moved, what's been cleaned, and what someone wanted left behind."
    vocabulary example: ["someone came back here", "this was staged", "the dust tells a different story"]

  SCI-FI / STATION:
  - Systems Operator → reads SYSTEM LOGS for sabotage signatures
  - Field Analyst → reads SENSOR DATA for anomalies
  - Scout → reads HULL / PHYSICAL SPACES for breaches and tampering

  HORROR / ASYLUM:
  - All roles → read RECORDS, TESTIMONY, and PHYSICAL TRACES for cover-ups

VOCABULARY RULES (enforce strictly):
- Exactly 3 phrases per character
- Each phrase is 3 to 6 words maximum — short, speakable, natural
- Must sound like something said during an INVESTIGATION in this world's genre
- FORBIDDEN in vocabulary: any word from the character's original job description
  Banned for tech roles: system, power, restore, circuit, override, scan, malfunction, panel, grid, reboot, diagnostic
  Banned for all: "check it out", "let's go", "be careful", "something amiss"
- Each character's vocabulary must sound DIFFERENT from every other character's

CONCRETE EXAMPLE — BAD vs GOOD:
Original: Riley Sato, Systems Operator. World: gothic manor mystery.

BAD (fails — copies original role, uses forbidden vocabulary):
  world_role: "A systems operator skilled in restoring old machinery to function properly."
  vocabulary: ["system check", "restore power", "practical solution"]

GOOD (correct — translated to manor mystery investigator):
  world_role: "Riley maps the sequence of events — who entered which room, in what order, and whether the timing points to premeditation."
  vocabulary: ["the timing is off", "someone planned this", "who had access"]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRICT ANTI-HALLUCINATION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. room_stories keys MUST be room ids from WORLD_DATA.rooms — no others.
2. discovery_beats keys MUST be object ids from WORLD_DATA.plot_objects — no others.
3. adapted_personas keys MUST be character names from WORLD_DATA.players — no others.
4. conversation_seeds keys MUST be character names from WORLD_DATA.players — no others.
5. The solution.answer MUST NOT be the name of any character in WORLD_DATA.players.
   The solution.answer MUST match exactly one suspect where is_killer=true.
   The solution.answer MUST equal mystery.killer_name exactly.
6. Do NOT reveal mechanical lock codes (numbers, sequences) in any narrative field.
   DO name the killer/answer in discovery_beats for clue_type='story_clue' objects
   and in conversation_seeds — this is how the human player learns the answer.
7. Do NOT invent new rooms, objects, or characters not in WORLD_DATA.
8. Each vocabulary list must contain exactly 3 short phrases — no full sentences.
9. Generate sections in this exact order: mystery → solution → suspects → discovery_beats → conversation_seeds → ending_guidance → plot → adapted_personas → room_stories.
10. mystery.proof_object_id MUST be an object_id from WORLD_DATA.plot_objects.
    It MUST match solution.proof_object exactly.
"""


# ── User prompt ────────────────────────────────────────────────────────────── #

def _build_user_prompt(setting: GameSetting, world_id: str, mystery: dict | None = None) -> str:
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

    proof_object_id = (mystery or {}).get("proof_object_id", "")

    def _clue_type(o) -> str:
        # The proof object is always a story_clue — it's the revelation moment
        # regardless of whether its info token contains digits.
        if proof_object_id and o.id == proof_object_id:
            return "story_clue"
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

    # Mystery anchor: authoritative ground truth that overrides LLM invention.
    mystery_anchor = ""
    if mystery and mystery.get("killer_name"):
        killer = mystery["killer_name"]
        victim = mystery.get("victim", "")
        proof_obj = mystery.get("proof_object_id", "")
        motive = mystery.get("motive_hint", "")
        mystery_anchor = (
            f"\nMYSTERY_ANCHOR — use these exact facts, do not invent alternatives:\n"
            f"  victim: {victim}\n"
            f"  killer_name: {killer}\n"
            f"  proof_object_id: {proof_obj}\n"
            f"  motive_hint: {motive}\n\n"
            f"MANDATORY RULES from MYSTERY_ANCHOR:\n"
            f"  - solution.answer MUST be exactly \"{killer}\" (no variation)\n"
            f"  - solution.proof_object MUST be exactly \"{proof_obj}\"\n"
            f"  - suspects MUST include one entry where name=\"{killer}\" and is_killer=true\n"
            f"  - discovery_beats[\"{proof_obj}\"] MUST explicitly name \"{killer}\"\n"
            f"  - plot.victim MUST describe the victim above\n"
            f"  - plot.tension_hint MUST reflect the motive above\n"
        )

    return (
        f"Generate the complete narrative storyboard for this escape room world.\n\n"
        f"WORLD_DATA:\n{json.dumps(world_data, indent=2)}\n"
        f"{mystery_anchor}\n"
        "STEP 1 — Decide the mystery (fill `mystery` block first):\n"
        "  - Pick the victim (a named person harmed before players arrived)\n"
        "  - Pick the killer (a plausible NPC — NOT any player in WORLD_DATA.players)\n"
        "  - Pick proof_object_id (one object_id with clue_type='story_clue' — this is the revelation item)\n"
        "  - Write the motive_hint (1 vague sentence)\n"
        "  Everything else MUST be consistent with this decision.\n\n"
        "STEP 2 — suspects: 2-3 suspects. Exactly one has is_killer=true (must be mystery.killer_name). "
        "Others are red herrings with plausible motives.\n\n"
        "STEP 3 — discovery_beats (physical description alone is REJECTED — must have investigative value):\n"
        "  clue_type='key': physical detail + what presence implies (timeline, access, urgency). No suspect names.\n"
        "    GOOD: 'The key is still warm — whoever placed it left this room within the hour.'\n"
        "    BAD:  'A key with carved ornaments.' ← description only, rejected\n"
        "  clue_type='story_clue' NOT proof: NAME one suspect from your suspects list. Connect evidence to their opportunity or motive.\n"
        "    IMPORTANT: use the actual name you invented in suspects — NOT 'Isabella Finch', NOT 'Jonathan Hale', NOT any example.\n"
        "    GOOD: '[your suspect name] — the monogram matches, and they claimed never to have been in this room.'\n"
        "    BAD:  'A key with an initial.' ← no suspect named, rejected\n"
        "  clue_type='story_clue' = mystery.proof_object_id: NAME mystery.killer_name (your actual killer). State what it proves.\n"
        "    IMPORTANT: use the actual killer name from your mystery block — NOT 'Isabella Finch', NOT any example.\n"
        "    GOOD: '[mystery.killer_name]'s signature is on this — they were here the night of the murder.'\n"
        "    BAD:  'A letter with smudged symbols.' ← killer not named, rejected\n"
        "  clue_type='code': atmosphere only, no numbers revealed.\n"
        "  clue_type='lock': skip entirely.\n\n"
        "Generate sections IN ORDER: mystery → solution → suspects → discovery_beats → conversation_seeds → ending_guidance → plot → adapted_personas → room_stories.\n"
        "conversation_seeds: exactly 2 items per character. RULES:\n"
        "  - Each seed MUST name at least one suspect from the suspects list by their full name.\n"
        "  - Seeds must NOT name the killer as the killer — name them as suspicious, not guilty.\n"
        "  - Mix: one seed that points toward a red-herring suspect, one that hints at the real killer's motive or opportunity.\n"
        "  IMPORTANT: use the actual names you invented in suspects — NOT 'Isabella Finch', NOT 'Jonathan Hale'.\n"
        "  GOOD: '[your suspect A] was the last to see the victim alive — their account has two gaps.'\n"
        "  GOOD: '[your suspect B] owed the victim a debt large enough to destroy them.'\n"
        "  BAD: 'The bloodstains suggest a violent struggle.' (no suspect named — rejected)\n"
        "  BAD: '[killer name] is the killer.' (names killer as guilty — rejected)\n"
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

    async def generate(
        self,
        setting: GameSetting,
        *,
        world_id: str = "",
        mystery: dict | None = None,
    ) -> Storyboard:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(setting, world_id, mystery)},
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

        data = self._sanitize_adapted_personas(data, setting)
        self._repair(data, world_id=world_id, mystery=mystery, setting=setting)
        self._validate(data, world_id=world_id, mystery=mystery)
        storyboard = Storyboard.from_dict(data)
        storyboard.world_id = world_id
        storyboard.generated_at = datetime.now(timezone.utc).isoformat()
        return storyboard

    @staticmethod
    def _s(v) -> str:
        """Coerce any LLM value to a plain string — guards against the model returning
        a nested dict or list where a string field was expected."""
        if isinstance(v, str):
            return v
        if v is None:
            return ""
        if isinstance(v, (list, dict)):
            return ""
        return str(v)

    def _repair(
        self, data: dict, *, world_id: str, mystery: dict | None, setting: "GameSetting | None" = None
    ) -> None:
        """Patch common LLM failures before the storyboard is built.

        Runs BEFORE _validate so the resulting Storyboard is always usable
        even when the model left critical sections blank.
        """
        gen_mystery = data.get("mystery") if isinstance(data.get("mystery"), dict) else {}
        solution = data.get("solution") if isinstance(data.get("solution"), dict) else {}

        # Authoritative killer / proof: world JSON anchor > LLM mystery > solution
        # _s() guards against the LLM returning dicts/lists for string fields.
        anchor_killer = self._s((mystery or {}).get("killer_name"))
        anchor_proof = self._s((mystery or {}).get("proof_object_id"))
        gen_killer = self._s(gen_mystery.get("killer_name"))
        gen_proof = self._s(gen_mystery.get("proof_object_id"))
        gen_victim = self._s(gen_mystery.get("victim")) or self._s((mystery or {}).get("victim"))
        gen_motive = self._s(gen_mystery.get("motive_hint")) or self._s((mystery or {}).get("motive_hint"))

        killer = anchor_killer or gen_killer or self._s(solution.get("answer"))
        proof = anchor_proof or gen_proof or self._s(solution.get("proof_object"))

        # ── validate proof is an object id, not a description ───────────────
        # The model sometimes copies the object description instead of its id.
        # An id is snake_case (no spaces); a description has spaces and is longer.
        valid_obj_ids: set[str] = set()
        if setting is not None:
            valid_obj_ids = {o.id for o in setting.objects}

        if proof and (" " in proof or (valid_obj_ids and proof not in valid_obj_ids)):
            # proof looks like a description — recover from known object ids or discovery_beats keys
            recovered = ""
            # Priority 1: match against valid setting objects with story_clue type
            if valid_obj_ids and setting is not None:
                # Find plot objects that are story_clues
                for o in setting.objects:
                    if o.id in valid_obj_ids and not o.id.startswith(("gate_", "scenic_", "filler_")):
                        if hasattr(o, "contains_info") and o.contains_info and not re.search(r"\d", o.contains_info):
                            recovered = o.id
                            break
            # Priority 2: look at discovery_beats keys for a beat already mentioning the killer
            if not recovered:
                beats = data.get("discovery_beats", {})
                if isinstance(beats, dict) and killer:
                    killer_last = killer.split()[-1]
                    for obj_id, beat_text in beats.items():
                        if " " not in obj_id and killer_last in self._s(beat_text):
                            recovered = obj_id
                            break
            if recovered:
                print(f"[StoryboardGenerator] REPAIR {world_id}: proof_object_id was a description — recovered {recovered!r}")
                proof = recovered
                # Clear the bad description value so the mystery/solution blocks write the correct id.
                if isinstance(data.get("mystery"), dict):
                    data["mystery"]["proof_object_id"] = ""
                if isinstance(data.get("solution"), dict):
                    data["solution"]["proof_object"] = ""

        # ── mystery block ────────────────────────────────────────────────────
        if "mystery" not in data or not isinstance(data["mystery"], dict):
            data["mystery"] = {}
        m = data["mystery"]
        if killer and not m.get("killer_name"):
            m["killer_name"] = killer
            print(f"[StoryboardGenerator] REPAIR {world_id}: mystery.killer_name → {killer!r}")
        if proof and not m.get("proof_object_id"):
            m["proof_object_id"] = proof
            print(f"[StoryboardGenerator] REPAIR {world_id}: mystery.proof_object_id → {proof!r}")

        # ── solution block ───────────────────────────────────────────────────
        if "solution" not in data or not isinstance(data["solution"], dict):
            data["solution"] = {}
        sol = data["solution"]
        if killer and not sol.get("answer"):
            sol["answer"] = killer
            print(f"[StoryboardGenerator] REPAIR {world_id}: solution.answer → {killer!r}")
        if proof and not sol.get("proof_object"):
            sol["proof_object"] = proof
            print(f"[StoryboardGenerator] REPAIR {world_id}: solution.proof_object → {proof!r}")
        if not sol.get("answer_type"):
            sol["answer_type"] = "person"
        if not sol.get("question") and killer:
            victim_name = gen_victim.split(",")[0] if gen_victim else "the victim"
            sol["question"] = f"Who murdered {victim_name}?"
        if not sol.get("motive") and gen_motive:
            sol["motive"] = gen_motive
        if not sol.get("hint_1") and killer:
            sol["hint_1"] = "Focus on who had both a reason and the opportunity — look at the relationships."
        if not sol.get("hint_2") and proof:
            sol["hint_2"] = f"The proof object holds the answer — check what it reveals about its owner."
        if not sol.get("victory_narration_hook") and killer:
            sol["victory_narration_hook"] = (
                f"The evidence points unmistakably to {killer} — every clue was a thread leading here."
            )
        if not sol.get("defeat_narration_hook") and killer:
            sol["defeat_narration_hook"] = (
                f"The truth stays buried. {killer} walks free while the manor keeps its secrets."
            )

        # ── plot block ───────────────────────────────────────────────────────
        # The model often leaves plot blank even when mystery is filled. Patch
        # from mystery data so the narrator has something to anchor tone to.
        if "plot" not in data or not isinstance(data["plot"], dict):
            data["plot"] = {}
        plot = data["plot"]
        if not plot.get("victim") and gen_victim:
            plot["victim"] = gen_victim
            print(f"[StoryboardGenerator] REPAIR {world_id}: plot.victim patched from mystery")
        if not plot.get("threat") and killer:
            plot["threat"] = (
                f"{killer}, whose guilt is concealed beneath a carefully arranged crime scene."
            )
        if not plot.get("stakes"):
            plot["stakes"] = (
                "The killer is still close — if the team fails to uncover the truth, they escape forever."
            )
        if not plot.get("tension_hint") and gen_motive:
            plot["tension_hint"] = gen_motive

        # ── suspects: normalize field names ──────────────────────────────────
        # The model uses various field names. Normalize to what suspects_context() expects.
        raw_suspects = data.get("suspects", [])
        if isinstance(raw_suspects, list):
            normalized = []
            for s in raw_suspects:
                if not isinstance(s, dict) or not s.get("name"):
                    continue
                name = s.get("name", "")
                connection = (
                    s.get("connection_to_victim")
                    or s.get("relationship_to_victim")
                    or s.get("description")
                    or ""
                )
                apparent_motive = (
                    s.get("apparent_motive")
                    or s.get("motive")
                    or ""
                )
                is_killer = s.get("is_killer", name == killer)
                # Back-fill apparent_motive if blank
                if not apparent_motive:
                    if is_killer and gen_motive:
                        apparent_motive = gen_motive
                    elif is_killer:
                        apparent_motive = "Their exact motive remains unclear — but they had the most to gain."
                    else:
                        apparent_motive = "Their connection to the victim gave them both means and opportunity."
                    print(f"[StoryboardGenerator] REPAIR {world_id}: suspects[{name!r}].apparent_motive patched")
                normalized.append({
                    "name": name,
                    "connection_to_victim": connection,
                    "apparent_motive": apparent_motive,
                    "is_killer": is_killer,
                })
            data["suspects"] = normalized

        # ── discovery_beats: normalize values to strings + patch proof beat ───
        discovery_beats = data.get("discovery_beats", {})
        if isinstance(discovery_beats, dict):
            # Coerce any dict/list values the LLM may have nested inside beats.
            data["discovery_beats"] = {
                k: self._s(v) for k, v in discovery_beats.items()
            }
            discovery_beats = data["discovery_beats"]
            if proof and killer:
                beat = discovery_beats.get(proof, "")
                killer_last = killer.split()[-1] if killer else ""
                if not beat or (killer_last and killer_last not in beat):
                    discovery_beats[proof] = (
                        f"The evidence here bears {killer}'s mark — "
                        f"this is the moment the investigation breaks open."
                    )
                    print(f"[StoryboardGenerator] REPAIR {world_id}: discovery_beats[{proof!r}] patched to name killer {killer!r}")

        # ── adapted_personas: fallback if model left them blank ─────────────
        if setting is not None:
            personas = data.get("adapted_personas")
            if not isinstance(personas, dict):
                data["adapted_personas"] = {}
                personas = data["adapted_personas"]
            for p in (setting.players or []):
                entry = personas.get(p.name)
                if not isinstance(entry, dict):
                    entry = {}
                    personas[p.name] = entry
                if not entry.get("world_role"):
                    entry["world_role"] = (
                        f"{p.name} investigates by reading the physical scene — "
                        f"looking for what was moved, what is missing, and what someone left behind."
                    )
                    print(f"[StoryboardGenerator] REPAIR {world_id}: adapted_personas[{p.name!r}].world_role patched")
                if not entry.get("voice"):
                    entry["voice"] = "Precise and observational — states what the evidence implies, not what they feel."
                if not entry.get("vocabulary"):
                    entry["vocabulary"] = ["who had access", "this was deliberate", "something is missing here"]

        # ── suspects: patch connection_to_victim if blank ────────────────────
        for s in data.get("suspects", []):
            if not isinstance(s, dict):
                continue
            if not s.get("connection_to_victim") and s.get("name"):
                name = s["name"]
                is_killer = s.get("is_killer", name == killer)
                s["connection_to_victim"] = (
                    "Closely connected to the victim and present at the scene."
                    if is_killer else
                    "Known to the victim and had recent contact before the crime."
                )

        # ── ending_guidance: fix wrong keys ─────────────────────────────────
        # Model sometimes generates descriptive keys instead of won/lost/lost_by_wrong_deduction.
        eg = data.get("ending_guidance", {})
        if isinstance(eg, dict):
            has_won = "won" in eg
            has_lost = "lost" in eg
            if not has_won and killer:
                # Look for any value that mentions the killer — that's the victory text
                for v in eg.values():
                    if isinstance(v, str) and killer.split()[0] in v:
                        eg["won"] = v
                        break
                if not eg.get("won"):
                    eg["won"] = sol.get("victory_narration_hook", f"The team exposes {killer}. Justice is served.")
            if not has_lost:
                eg["lost"] = sol.get("defeat_narration_hook", "Time runs out. The truth stays buried.")
            if "lost_by_wrong_deduction" not in eg and killer:
                eg["lost_by_wrong_deduction"] = f"The wrong name was called. {killer} slips away."
            data["ending_guidance"] = eg

    def _validate(
        self, data: dict, *, world_id: str, mystery: dict | None
    ) -> None:
        """Log warnings for incomplete or contradictory storyboard data."""
        solution = data.get("solution", {})
        generated_mystery = data.get("mystery", {}) if isinstance(data.get("mystery"), dict) else {}
        answer = solution.get("answer", "")
        proof_obj = solution.get("proof_object", "")
        suspects = [s for s in data.get("suspects", []) if isinstance(s, dict)]

        if not answer:
            print(f"[StoryboardGenerator] WARNING {world_id}: solution.answer is empty")
        if not proof_obj:
            print(f"[StoryboardGenerator] WARNING {world_id}: solution.proof_object is empty")
        if len(suspects) < 2:
            print(f"[StoryboardGenerator] WARNING {world_id}: only {len(suspects)} suspect(s) generated")

        # Check generated mystery block is present and internally consistent.
        if not generated_mystery.get("killer_name"):
            print(f"[StoryboardGenerator] WARNING {world_id}: mystery.killer_name is empty")
        if not generated_mystery.get("proof_object_id"):
            print(f"[StoryboardGenerator] WARNING {world_id}: mystery.proof_object_id is empty")
        gen_killer = generated_mystery.get("killer_name", "")
        gen_proof = generated_mystery.get("proof_object_id", "")
        if gen_killer and answer and gen_killer != answer:
            print(
                f"[StoryboardGenerator] WARNING {world_id}: mystery.killer_name={gen_killer!r} "
                f"does not match solution.answer={answer!r} — auto-aligning"
            )
            data["mystery"]["killer_name"] = answer
        if gen_proof and proof_obj and gen_proof != proof_obj:
            print(
                f"[StoryboardGenerator] WARNING {world_id}: mystery.proof_object_id={gen_proof!r} "
                f"does not match solution.proof_object={proof_obj!r} — auto-aligning"
            )
            data["mystery"]["proof_object_id"] = proof_obj

        # If a world-JSON mystery anchor was given, verify the LLM respected it.
        if mystery:
            expected_answer = mystery.get("killer_name", "")
            expected_proof = mystery.get("proof_object_id", "")
            if expected_answer and answer != expected_answer:
                print(
                    f"[StoryboardGenerator] WARNING {world_id}: solution.answer={answer!r} "
                    f"does not match mystery anchor killer_name={expected_answer!r}"
                )
            if expected_proof and proof_obj != expected_proof:
                print(
                    f"[StoryboardGenerator] WARNING {world_id}: solution.proof_object={proof_obj!r} "
                    f"does not match mystery anchor proof_object_id={expected_proof!r}"
                )

        # Verify killer appears in suspects with is_killer=true.
        if answer and suspects:
            killer_confirmed = any(
                s.get("name") == answer and s.get("is_killer") is True
                for s in suspects
            )
            if not killer_confirmed:
                print(
                    f"[StoryboardGenerator] WARNING {world_id}: killer {answer!r} "
                    f"not found in suspects with is_killer=true"
                )

    # Forbidden words that indicate genre contamination from a tech/mechanical role.
    # If any vocabulary phrase contains these, the storyboard model failed to adapt.
    _CONTAMINATION_WORDS = frozenset({
        "system", "power", "restore", "circuit", "override", "scan",
        "malfunction", "panel", "grid", "reboot", "diagnostic", "check",
        "override", "mainframe", "electrical", "machinery",
    })

    def _sanitize_adapted_personas(
        self, data: dict, setting: GameSetting
    ) -> dict:
        """Post-process adapted_personas: detect and warn about contaminated vocabulary.

        If a character's vocabulary still contains tech/mechanical words from their
        original role, log a warning and remove the offending phrases so the injection
        doesn't make things worse than the unmodified persona would have.
        """
        personas = data.get("adapted_personas")
        if not isinstance(personas, dict):
            return data

        player_roles = {p.name: p.role for p in (setting.players or [])}

        for name, persona in personas.items():
            if not isinstance(persona, dict):
                continue
            vocab = persona.get("vocabulary")
            if not isinstance(vocab, list):
                continue

            clean: list[str] = []
            for phrase in vocab:
                phrase_lower = phrase.lower()
                if any(word in phrase_lower for word in self._CONTAMINATION_WORDS):
                    orig_role = player_roles.get(name, "")
                    print(
                        f"[StoryboardGenerator] WARNING: contaminated vocabulary for "
                        f'"{name}" (original role: {orig_role!r}): {phrase!r} — removed'
                    )
                else:
                    clean.append(phrase)

            # Also warn if world_role looks like a copy of the original.
            world_role = persona.get("world_role", "")
            orig_role = player_roles.get(name, "").lower()
            if orig_role and orig_role[:20] in world_role.lower():
                print(
                    f"[StoryboardGenerator] WARNING: world_role for {name!r} "
                    f"appears to copy the original role — adaptation may have failed."
                )

            persona["vocabulary"] = clean

        return data


def make_storyboard_client(base_url: str | None = None) -> OllamaClient:
    """Construct the Ollama client configured for storyboard generation."""
    from app.llm.ollama_client import DEFAULT_OLLAMA_URL
    return OllamaClient(
        model=STORYBOARD_MODEL,
        base_url=base_url or DEFAULT_OLLAMA_URL,
        timeout=300.0,  # 5-minute timeout — storyboard generation can be slow
    )
