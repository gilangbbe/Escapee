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


# ── Pass 1: core facts (mystery / solution / suspects) ────────────────────── #

_CORE_SYSTEM = """\
You are a STORY ARCHITECT for a murder mystery escape room game.
You receive WORLD_DATA (rooms, objects, players). You output ONE JSON object with
exactly three sections: "mystery", "solution", "suspects". Nothing else.

{
  "mystery": {
    "victim": "FULL NAME of the person killed + one clause on how they died. NEVER leave blank.",
    "killer_name": "Full name of the murderer. Invent a name that fits this world. NEVER leave blank.",
    "proof_object_id": "COPY the exact `id` of ONE WORLD_DATA.plot_objects entry with clue_type='story_clue'. The id string, NOT the description.",
    "motive_hint": "1 sentence. The killer's motive — specific but not spoiling."
  },
  "solution": {
    "question": "'Who murdered [victim's full name]?'",
    "answer": "Must equal mystery.killer_name exactly.",
    "answer_aliases": ["surname only", "title plus surname"],
    "answer_type": "person",
    "motive": "2 sentences. Full explanation, shown only after the win.",
    "proof_object": "Must equal mystery.proof_object_id exactly.",
    "proof_sentence": "1 sentence. What that evidence specifically proves.",
    "hint_1": "Vague directional hint after the first wrong guess.",
    "hint_2": "More specific hint after the second wrong guess — still no name.",
    "victory_narration_hook": "1 sentence naming the killer and the proof.",
    "defeat_narration_hook": "1 sentence for the defeat ending."
  },
  "suspects": [
    {"name": "...", "connection_to_victim": "1 sentence", "apparent_motive": "1 sentence", "is_killer": true},
    {"name": "...", "connection_to_victim": "1 sentence", "apparent_motive": "1 sentence", "is_killer": false}
  ]
}

RULES:
- victim and killer_name are the two most important fields — fill them FIRST, never blank.
- 2-3 suspects. Exactly ONE has is_killer=true and their name equals mystery.killer_name.
- Red-herring motives must be genuinely plausible. Vary them: financial / personal / opportunistic.
- Suspects must NOT be characters from WORLD_DATA.players (those are the investigators).
- Invent names that fit THIS world's scenario. Do NOT reuse any name from these instructions.
- Output raw JSON only — no markdown, no commentary.
"""


# ── Pass 2: clue layer (discovery_beats / conversation_seeds / ending) ─────── #

_BEATS_SYSTEM = """\
You are a CLUE WRITER for a murder mystery escape room game.
You receive WORLD_DATA plus CASE_FACTS (victim, killer, suspects — already decided, immutable).
You output ONE JSON object with exactly three sections:
"discovery_beats", "conversation_seeds", "ending_guidance". Nothing else.

{
  "discovery_beats": { "<object_id from WORLD_DATA.plot_objects>": "2 sentences", ... },
  "conversation_seeds": { "<player name from WORLD_DATA.players>": ["seed 1", "seed 2"], ... },
  "ending_guidance": {
    "won": "2 sentences. Victory close — names the killer (CASE_FACTS.killer_name).",
    "lost": "2 sentences. Failure close — the truth stays buried.",
    "lost_by_wrong_deduction": "1 sentence. The real killer slips away."
  }
}

DISCOVERY_BEATS RULES (one beat per plot_object, EXCEPT clue_type='lock' — skip those):
- Every beat must implicate someone or reveal a fact. Pure object description is REJECTED.
- THE GOLDEN RULE: only the beat for CASE_FACTS.proof_object_id names the killer.
- clue_type='key': who had access or used it recently. May name a NON-killer suspect.
- clue_type='story_clue' (not proof): MUST name one NON-killer suspect — tie the evidence
  to their presence, motive, or opportunity. May be a red herring.
- clue_type='story_clue' that IS proof_object_id: MUST name CASE_FACTS.killer_name.
  State exactly what it proves. This is the climax beat.
- clue_type='code': who created or studied this document. May name a NON-killer suspect.
  NEVER reveal numbers or code values.

CONVERSATION_SEEDS RULES (exactly 2 per player):
- Each seed names at least one suspect (full name) and ties evidence or timing to them.
- Never state who is guilty. At least one seed per player points at a red herring.

Use ONLY the names that appear in CASE_FACTS. Output raw JSON only.
"""


# ── Pass 3: flavor layer (plot / adapted_personas / room_stories) ──────────── #

_FLAVOR_SYSTEM = """\
You are a NARRATIVE DESIGNER for a murder mystery escape room game.
You receive WORLD_DATA plus CASE_FACTS (victim, killer, motive — already decided, immutable).
You output ONE JSON object with exactly three sections:
"plot", "adapted_personas", "room_stories". Nothing else.

{
  "plot": {
    "victim": "1 sentence — who was harmed, when, how. Use CASE_FACTS.victim.",
    "threat": "1 sentence — the concrete danger still present.",
    "stakes": "1 sentence — what failure costs. Do NOT name the killer.",
    "timeline": "2 sentences — key events before the players arrived.",
    "tension_hint": "1 sentence — vague nod to the hidden motive.",
    "atmosphere": "2 sentences — concrete sensory detail: smell, sound, light, temperature.",
    "protagonist_context": "1 sentence — why these specific people are here."
  },
  "adapted_personas": {
    "<player name from WORLD_DATA.players>": {
      "world_role": "1-2 sentences. HOW they investigate in this genre — their lens on clues. NOT their job title.",
      "voice": "1 sentence. A speaking style distinct from every other character.",
      "vocabulary": ["3-6 word phrase", "3-6 word phrase", "3-6 word phrase"]
    }
  },
  "room_stories": { "<room_id from WORLD_DATA.rooms>": "1 sentence — this room's narrative role." }
}

ADAPTED_PERSONAS RULES:
- Translate each player's mechanical role into an investigator archetype for THIS genre:
  Systems Operator → reads the SEQUENCE of events: timing, who was where, premeditation
  Field Analyst → reads PHYSICAL EVIDENCE: what objects imply about intent
  Scout → reads the ENVIRONMENT: what was moved, cleaned, or left behind
- vocabulary: exactly 3 phrases, 3-6 words each, natural investigation speech for this genre.
  BANNED words: system, power, restore, circuit, override, scan, malfunction, panel, grid, reboot, diagnostic.
- Each character must sound DIFFERENT from every other character.

room_stories: one entry per room id in WORLD_DATA.rooms — no invented rooms.
Output raw JSON only.
"""


# ── World data + pass user prompts ────────────────────────────────────────── #

def _collect_world_data(setting: GameSetting, world_id: str, mystery: dict | None = None) -> dict:
    """Distill the GameSetting into the compact WORLD_DATA dict all passes share."""
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

    return world_data


def _core_user_prompt(world_data: dict, mystery: dict | None) -> str:
    anchor = ""
    if mystery and mystery.get("killer_name"):
        anchor = (
            "\nMYSTERY_ANCHOR — these facts are fixed, use them exactly:\n"
            + json.dumps(mystery, indent=2) + "\n"
        )
    return (
        "Decide the mystery for this world.\n\n"
        f"WORLD_DATA:\n{json.dumps(world_data, indent=2)}\n"
        f"{anchor}\n"
        "Choose the victim, the killer, the proof object, and 2-3 suspects. "
        "proof_object_id must be the `id` of a plot_objects entry with clue_type='story_clue'. "
        "victim and killer_name must NEVER be blank. Output the JSON now."
    )


def _beats_user_prompt(world_data: dict, case_facts: dict) -> str:
    slim = {k: world_data[k] for k in ("scenario", "players", "plot_objects", "unlock_chain") if k in world_data}
    return (
        "Write the clue layer for this murder mystery.\n\n"
        f"WORLD_DATA:\n{json.dumps(slim, indent=2)}\n\n"
        f"CASE_FACTS (immutable — use these exact names):\n{json.dumps(case_facts, indent=2)}\n\n"
        "Write one discovery beat per plot_object (skip clue_type='lock'), "
        "2 conversation seeds per player, and the ending_guidance. "
        "Remember THE GOLDEN RULE: only the proof object beat names the killer. Output the JSON now."
    )


def _flavor_user_prompt(world_data: dict, case_facts: dict) -> str:
    slim = {k: world_data[k] for k in ("scenario", "objective", "rooms", "players") if k in world_data}
    facts = {k: case_facts.get(k, "") for k in ("victim", "killer_name", "motive_hint")}
    return (
        "Write the narrative flavor layer for this murder mystery.\n\n"
        f"WORLD_DATA:\n{json.dumps(slim, indent=2)}\n\n"
        f"CASE_FACTS (immutable):\n{json.dumps(facts, indent=2)}\n\n"
        "Write plot, adapted_personas (one per player), and room_stories (one per room id). "
        "Output the JSON now."
    )


# ── Generator ──────────────────────────────────────────────────────────────── #

class StoryboardGenerator:
    """Generates a Storyboard from any GameSetting via three focused LLM passes.

    Pass 1 decides the core facts (mystery/solution/suspects), pass 2 writes the
    clue layer against those facts, pass 3 writes the flavor layer. Splitting
    keeps each output small enough that a local 14B model cannot drop fields.
    Degrades gracefully to an empty Storyboard on any error.
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
        """Three focused passes instead of one giant JSON document.

        A local 14B model drops random fields when asked for everything at once
        (attention degrades as output grows). Each pass is small enough that the
        core facts cannot get lost, and passes 2-3 receive pass 1's decisions as
        immutable CASE_FACTS so they cannot contradict them.
        """
        world_data = _collect_world_data(setting, world_id, mystery)
        data: dict = {}

        # PASS 1 — core facts. Small output: victim/killer cannot be dropped.
        core = await self._call_json(_CORE_SYSTEM, _core_user_prompt(world_data, mystery))
        if core:
            data.update({k: core[k] for k in ("mystery", "solution", "suspects") if k in core})
        else:
            print(f"[StoryboardGenerator] {world_id}: core pass failed — relying on repair")

        case_facts = self._case_facts(data, mystery)

        # PASS 2 — clue layer, grounded in the now-fixed case facts.
        beats = await self._call_json(_BEATS_SYSTEM, _beats_user_prompt(world_data, case_facts))
        if beats:
            data.update({
                k: beats[k]
                for k in ("discovery_beats", "conversation_seeds", "ending_guidance")
                if k in beats
            })
        else:
            print(f"[StoryboardGenerator] {world_id}: beats pass failed — relying on repair")

        # PASS 3 — flavor layer.
        flavor = await self._call_json(_FLAVOR_SYSTEM, _flavor_user_prompt(world_data, case_facts))
        if flavor:
            data.update({
                k: flavor[k]
                for k in ("plot", "adapted_personas", "room_stories")
                if k in flavor
            })
        else:
            print(f"[StoryboardGenerator] {world_id}: flavor pass failed — relying on repair")

        data = self._sanitize_adapted_personas(data, setting)
        self._repair(data, world_id=world_id, mystery=mystery, setting=setting)
        self._validate(data, world_id=world_id, mystery=mystery)
        storyboard = Storyboard.from_dict(data)
        storyboard.world_id = world_id
        storyboard.generated_at = datetime.now(timezone.utc).isoformat()
        return storyboard

    async def _call_json(self, system: str, user: str) -> dict | None:
        """One LLM call returning a parsed JSON object, or None on any failure."""
        try:
            raw = await self.client.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                json_mode=True,
                temperature=self.temperature,
            )
        except Exception as exc:
            print(f"[StoryboardGenerator] LLM call failed: {exc}")
            return None
        if not raw:
            return None
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.splitlines()[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.splitlines()[:-1])
        try:
            parsed = json.loads(cleaned)
        except Exception as exc:
            print(f"[StoryboardGenerator] JSON parse failed: {exc}")
            return None
        return parsed if isinstance(parsed, dict) else None

    def _case_facts(self, data: dict, mystery: dict | None) -> dict:
        """Extract the immutable case facts from pass 1 (anchor takes precedence)."""
        m = data.get("mystery") if isinstance(data.get("mystery"), dict) else {}
        sol = data.get("solution") if isinstance(data.get("solution"), dict) else {}
        suspects = data.get("suspects") if isinstance(data.get("suspects"), list) else []
        anchor = mystery or {}
        return {
            "victim": self._s(anchor.get("victim")) or self._s(m.get("victim")),
            "killer_name": (
                self._s(anchor.get("killer_name"))
                or self._s(m.get("killer_name"))
                or self._s(sol.get("answer"))
            ),
            "proof_object_id": (
                self._s(anchor.get("proof_object_id"))
                or self._s(m.get("proof_object_id"))
                or self._s(sol.get("proof_object"))
            ),
            "motive_hint": self._s(anchor.get("motive_hint")) or self._s(m.get("motive_hint")),
            "suspects": [
                {"name": self._s(s.get("name")), "is_killer": bool(s.get("is_killer"))}
                for s in suspects
                if isinstance(s, dict) and s.get("name")
            ],
        }

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
        # ── normalize list-shaped maps the LLM sometimes emits ──────────────
        # e.g. room_stories: [{"room_id": "study", "story": "..."}] → {"study": "..."}
        for map_key, id_keys, text_keys in (
            ("room_stories", ("room_id", "id", "room"), ("story", "text", "description")),
            ("discovery_beats", ("object_id", "id", "object"), ("beat", "story", "text", "description")),
        ):
            raw_map = data.get(map_key)
            if isinstance(raw_map, list):
                converted: dict = {}
                for item in raw_map:
                    if not isinstance(item, dict):
                        continue
                    item_id = next((self._s(item.get(k)) for k in id_keys if item.get(k)), "")
                    item_text = next((self._s(item.get(k)) for k in text_keys if item.get(k)), "")
                    if item_id and item_text:
                        converted[item_id] = item_text
                data[map_key] = converted
                print(f"[StoryboardGenerator] REPAIR {world_id}: {map_key} was a list — converted {len(converted)} entries to dict")

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
        # Extract victim from solution.question ("Who murdered X?") if mystery.victim is blank.
        if not m.get("victim"):
            sol_q = self._s(solution.get("question"))
            victim_match = re.search(r"[Ww]ho (?:murdered|killed|harmed) ([^?]+)\?", sol_q)
            if victim_match:
                m["victim"] = victim_match.group(1).strip()
                gen_victim = m["victim"]
                print(f"[StoryboardGenerator] REPAIR {world_id}: mystery.victim extracted from question → {m['victim']!r}")

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
        if not sol.get("proof_sentence") and proof and killer:
            proof_readable = proof.replace("_", " ")
            sol["proof_sentence"] = (
                f"The {proof_readable} ties {killer} directly to the crime — "
                f"physical evidence that places them at the scene."
            )
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
            _persona_archetypes = [
                # (world_role_template, voice, vocabulary)
                (
                    "{name} reads the physical scene for what it implies about intent — "
                    "the angle of a disturbed object, what's been moved, what someone tried to hide.",
                    "Precise and dry — states what the evidence implies, never what they feel.",
                    ["this wasn't accidental", "look at what's missing", "the evidence contradicts that"],
                ),
                (
                    "{name} maps the sequence of events — who was where, in what order, "
                    "and whether the timing points to premeditation.",
                    "Methodical and measured — frames observations as timelines, not accusations.",
                    ["the timing is off", "who had access", "someone planned this"],
                ),
                (
                    "{name} reads spaces before touching anything — spots what was moved, "
                    "cleaned, or deliberately left behind.",
                    "Quiet and observational — notices what others overlook, asks questions instead of statements.",
                    ["something was staged here", "this was cleaned in a hurry", "someone came back"],
                ),
            ]
            personas = data.get("adapted_personas")
            if not isinstance(personas, dict):
                data["adapted_personas"] = {}
                personas = data["adapted_personas"]
            for idx, p in enumerate((setting.players or [])):
                entry = personas.get(p.name)
                if not isinstance(entry, dict):
                    entry = {}
                    personas[p.name] = entry
                archetype = _persona_archetypes[idx % len(_persona_archetypes)]
                if not entry.get("world_role"):
                    entry["world_role"] = archetype[0].format(name=p.name)
                    print(f"[StoryboardGenerator] REPAIR {world_id}: adapted_personas[{p.name!r}].world_role patched")
                if not entry.get("voice"):
                    entry["voice"] = archetype[1]
                if not entry.get("vocabulary"):
                    entry["vocabulary"] = list(archetype[2])

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
