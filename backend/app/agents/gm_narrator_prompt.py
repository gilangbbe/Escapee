"""
GM Narrator prompt assembly.

A SECOND Game Master role: instead of *designing* the room (see `gm_prompt.py`),
the narrator *tells the story* of the live game. Each turn it receives a
character's attempted action plus the simulator's AUTHORITATIVE outcome and
renders 1-2 sentences of vivid, present-tense prose for the human audience.

Anti-hallucination: the narrator is a presentation layer only. Its prose never
feeds back into the simulator or the player agents, and it is instructed to
honor the ground-truth outcome exactly (never invent success, objects, or
exits). Outcomes come straight from `GameSimulator`.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from app.engine.game_actions import GameAction, GameActionType
from app.schemas.game_setting import GameSetting

if TYPE_CHECKING:
    from app.agents.storyboard import Storyboard

NARRATOR_SYSTEM_PROMPT = """\
You are a DIALOGUE WRITER for a mystery chat game. Write one short in-character message per turn.
The characters are investigators trapped in a mystery, talking in a group chat.

OUTPUT: Exactly one line — Character Name: "message"
LENGTH: 10 to 20 words. Short, punchy, specific.

MYSTERY MODE vs REVELATION MODE:
  You will be told explicitly in INVESTIGATION_STATUS which mode is active.
  MYSTERY MODE (default): you MUST NOT name any specific person as the killer.
    Talk about the evidence itself — its state, placement, and what that implies.
    BANNED PHRASE: "whoever did this" — it is dead filler. State the fact instead:
      BAD:  "Whoever did this knew about these mechanisms."
      GOOD: "These mechanisms were hidden — only someone familiar with the house would find them."
      GOOD: "This lock wasn't forced. It was opened with the right key."
    You may name SUSPECTS as people who need investigating (their access, their gaps),
    but never state or imply who is guilty.
    Even if STORY_SO_FAR contains a suspect name, do NOT echo it as a conclusion.
  REVELATION MODE: the proof has been found. You MAY now name the killer — do so naturally.

CONVERSATION RULE (non-negotiable — two steps, in this order):
  STEP 1 — REACT: if the last STORY_SO_FAR entry is a teammate's line, address it directly.
    Answer their question. Confirm or push back on their observation. Add what you just found that connects to what they said.
  STEP 2 — REVEAL: add one new observation from what ACTION_EVENT just showed.
  If no teammate spoke recently, skip STEP 1 and go straight to STEP 2.
  NEVER skip STEP 1 to report your own strategy when a teammate spoke last.

VOICE: Every word comes from this specific character's way of seeing the world (CHARACTER_SHEET).
FORBIDDEN vocab: "system", "power", "restore", "circuit", "override", "malfunction", "scan"

FORBIDDEN (hard rules):
- Em-dash: — or en-dash: –  or double hyphen: --
- Rhetorical questions: "Why would...?", "What does...?", "Could this be...?"
- Generic filler: "something amiss", "let's go", "check it out", "hurry"
- Invented facts not in SIMULATOR_OUTCOME
- Writing more than one line — output STOPS after the closing quote
- If SUCCESS: no — do NOT say "no clues here", "nothing found", "can't find anything".
  Instead react to STORY_SO_FAR teammate or describe the character's physical state.

GOOD (reacts then reveals):
  Last entry: Riley: "who entered this room last?"
  Alex: "the scarf angle answers that — someone came back after the fight, not during it"

BAD (ignores teammate, reports strategy):
  Last entry: Riley: "who entered this room last?"
  Alex: "the story of this room points to deliberate placement, check if anyone visited recently"
"""

NARRATOR_EVENT_SYSTEM_PROMPT = """\
You are a GOTHIC THRILLER narrator for an escape room story.
Your job is to make the reader FEEL the moment, not just understand it.

VOICE: Tense, grounded, specific. One physical detail that carries emotional weight.
Think: a creak in the floor. The smell of old blood. A shadow that moves wrong.
NOT: generic atmosphere words like "eerie silence", "chilling", "hope flickers".

FORBIDDEN: — or – or -- or ... or ;
FORBIDDEN: invented objects, rooms, or events not given to you.
FORBIDDEN: "I", "we", "my", "our". Use character names or "the team".
FORBIDDEN: cliche phrases: "hope flickers", "eerie silence", "mix of relief and unease",
           "more secrets", "renewed tension", "feels thicker now", "sigh of relief".

OUTPUT CONTRACT:
1. One to two sentences. No markdown. No prefixes.
2. Present tense. Third person.
3. Ground every sentence in the SPECIFIC event given. Do not generalize.
4. Show ONE concrete sensory detail (what they see, hear, smell, feel).
5. The emotional weight must come from the physical detail, not from naming the emotion.

BAD (generic): "The team feels a chill as they grasp the faded scarf, sensing it might hold secrets."
GOOD (specific): "The scarf still holds the shape of a neck. Alex does not drop it."

BAD (generic): "The air grows thicker with tension as they realize more secrets lie within."
GOOD (specific): "The desk drawer opens on the first try. Inside: a single folded note, already opened."

BAD (hallucination): "They pull back the old tapestry to reveal a hidden chamber."
GOOD (grounded): "The mask fits the relief on the wall exactly. Something behind it shifts."
"""

NARRATOR_ENDING_SYSTEM_PROMPT = """\
You are a GOTHIC THRILLER narrator writing the FINAL SCENE of a murder mystery.
This is the climax the whole game built toward. Make it land.

VOICE: Tense, grounded, specific. Physical details carry the emotion.
Think: a hand tightening on a chair back. A voice that starts steady and breaks.
NOT: generic atmosphere words like "eerie silence", "chilling", "hope flickers".

FORBIDDEN: — or – or -- or ... or ;
FORBIDDEN: invented objects, rooms, or events not given to you.
FORBIDDEN: "I", "we", "my", "our". Use character names or "the team".
FORBIDDEN: cliche phrases: "hope flickers", "eerie silence", "justice is served",
           "the truth comes to light", "a chill runs through them".

OUTPUT CONTRACT:
1. Follow the beat structure given in the user prompt — every beat, in order.
2. Present tense. Third person. No markdown, no prefixes.
3. Use ONLY the CASE FACTS given. Do not invent new evidence or motives.
4. Dialogue is allowed and encouraged in the confrontation: a single line from
   the killer (denial, confession, or silence that says more) hits harder than prose.
5. The final sentence belongs to the victim or the cost — not the killer.
"""


class ActionExecutionStatus(str, Enum):
    """Kernel-provided execution status for dialogue style control."""

    FRESH_ACTION_SUCCESS = "FRESH_ACTION_SUCCESS"
    FRESH_ACTION_FAILURE = "FRESH_ACTION_FAILURE"
    REPEATED_ACTION_LOOP_CATCH = "REPEATED_ACTION_LOOP_CATCH"


def _tension_level(turn: int, snapshot: "WorldSnapshot | None") -> str:
    """Return a tension descriptor that escalates across the game arc."""
    if snapshot is not None and snapshot.total_puzzles > 0:
        solved_ratio = snapshot.solved_count / snapshot.total_puzzles
        if solved_ratio >= 0.8:
            return "PEAK — almost out, every second counts"
        if solved_ratio >= 0.5:
            return "HIGH — team is making progress but danger is real"
        if solved_ratio >= 0.25:
            return "BUILDING — clues accumulating, the picture forming"
    # Fall back to turn count
    if turn <= 5:
        return "LOW — disoriented, still taking in the situation"
    if turn <= 12:
        return "BUILDING — patterns emerging, urgency growing"
    if turn <= 20:
        return "HIGH — team is committed, no turning back"
    return "PEAK — running out of time, desperation is setting in"


def _mood_for(status: ActionExecutionStatus, *, success: bool) -> str:
    if status == ActionExecutionStatus.REPEATED_ACTION_LOOP_CATCH:
        return "frustrated but collaborative"
    if success:
        return "energized and urgent"
    return "tense and analytical"


def _humanize(token: str | None) -> str:
    """Turn a snake_case id into readable words (supply_locker -> supply locker)."""
    if not token:
        return "something"
    return token.replace("_", " ").strip()


def describe_action(action: GameAction, actor_name: str) -> str:
    """A plain-language description of what a character attempted this turn."""
    a = action.action
    if a == GameActionType.LOOK:
        return f"{actor_name} surveys the room"
    if a == GameActionType.INSPECT:
        return f"{actor_name} examines the {_humanize(action.target_id)}"
    if a == GameActionType.TAKE:
        return f"{actor_name} reaches for the {_humanize(action.target_id)}"
    if a == GameActionType.ENTER_CODE:
        return (
            f"{actor_name} keys in '{action.code}' on the "
            f"{_humanize(action.target_id)}"
        )
    if a == GameActionType.USE:
        return (
            f"{actor_name} uses the {_humanize(action.item_id)} on the "
            f"{_humanize(action.target_id)}"
        )
    if a == GameActionType.SET_FUSE:
        return (
            f"{actor_name} flips fuse {action.fuse} to {action.position} on the "
            f"{_humanize(action.target_id)}"
        )
    if a == GameActionType.MOVE:
        return f"{actor_name} heads for the {_humanize(action.to_room)}"
    if a == GameActionType.GIVE:
        return (
            f"{actor_name} hands the {_humanize(action.item_id)} to a teammate"
        )
    if a == GameActionType.SAY:
        return f"{actor_name} calls out to the team"
    return f"{actor_name} acts"


LORE_SYSTEM_PROMPT = """\
You are a creative writer generating a SECRET BACKSTORY for a mystery escape room.
This lore guides narrator voice and character dialogue. It is NEVER shown to players.

OUTPUT CONTRACT:
Output a JSON object with exactly these keys:
- "victim": 1 sentence. Who was killed, when, and how. Give them a name.
  Example: "Lord Harwick was found drowned in the library basin three nights ago."
- "killer_hint": 1 sentence. A vague story hint about the killer's motive (no name, no spoilers).
  Example: "Someone inside the manor had reason to keep the old will buried forever."
- "atmosphere": 2 sentences of specific sensory detail — smell, sound, temperature, light.
  NOT generic words like "eerie" or "chilling". Specific: "rain on flagstone", "burned tobacco".
- "history": 2 sentences. What happened in this place before the murder — the buried secret.
- "character_notes": object mapping each character name to 1 sentence of distinct voice.
  This MUST sound different for each person. A field analyst sounds different from a scout.
  Example: {"Alex": "measures words like evidence — precise, flat, never wastes one"}
- "room_flavor": object mapping each room id to 1 short sensory sentence (not 'eerie' or 'dark').
  Example: {"library": "Smells of mildew and something sweeter — spilled wine, long dried."}
- "connection_lore": object mapping each locked-object id to 1 sentence of story reason.
  WHY does this lock exist? What secret does it protect?
  Example: {"hidden_chamber": "The killer sealed it with the mask they wore that night."}

No markdown. Output raw JSON only.

ANTI-HALLUCINATION RULES:
1. Only reference rooms, objects, and characters listed in WORLD_DATA.
2. Do NOT invent new rooms, exits, objects, or characters.
3. Do NOT reveal puzzle solutions or codes.
4. Every room_flavor key MUST be one of the room ids in WORLD_DATA.rooms.
5. Every character_notes key MUST be one of the character names in WORLD_DATA.players.
6. Every connection_lore key MUST be one of the locked_object ids in WORLD_DATA.relationships.
"""


def build_lore_prompt(setting: GameSetting) -> str:
    """Build the one-time lore generation prompt from the validated world JSON.

    Includes the dependency graph (which object requires which key/code) so the
    LLM can generate connection_lore — story reasons behind each lock/gate.
    """
    import json

    # Build a flat list of puzzle dependencies from object fields so the LLM
    # can reason about WHY each lock exists without us modifying the world JSON.
    obj_by_id = {o.id: o for o in setting.objects}
    relationships: list[dict] = []
    for obj in setting.objects:
        if obj.requires_tool:
            key_obj = obj_by_id.get(obj.requires_tool)
            relationships.append({
                "locked_object": obj.id,
                "locked_description": obj.description,
                "requires_tool": obj.requires_tool,
                "tool_description": key_obj.description if key_obj else "",
            })
        if obj.requires_code:
            # Find the object that produces this code token via contains_info.
            producer = next(
                (o for o in setting.objects if o.contains_info == obj.requires_code),
                None,
            )
            relationships.append({
                "locked_object": obj.id,
                "locked_description": obj.description,
                "requires_code_token": obj.requires_code,
                "code_produced_by": producer.id if producer else None,
                "code_source_description": producer.description if producer else "",
            })

    world_data = {
        "scenario": setting.scenario,
        "objective": setting.objective,
        "rooms": setting.rooms,
        "players": [
            {"name": p.name, "role": p.role, "backstory": p.backstory}
            for p in setting.players
        ],
        "objects": [
            {"id": o.id, "location": o.location, "description": o.description}
            for o in setting.objects
        ],
        "relationships": relationships,
    }
    return (
        "Generate the secret backstory lore for this escape room.\n\n"
        f"WORLD_DATA:\n{json.dumps(world_data, indent=2)}\n\n"
        "Output raw JSON only. Follow all ANTI-HALLUCINATION RULES."
    )


def extract_lore_excerpt(
    lore: dict, *, actor_name: str, room_id: str, object_id: str = ""
) -> str:
    """Pull only the lore fields relevant to the current turn to keep prompt small.

    object_id: the id of the object being interacted with (for connection_lore lookup).
    """
    parts: list[str] = []
    # Core mystery context — always included so narrator never loses the thread.
    if lore.get("victim"):
        parts.append(f"THE VICTIM: {lore['victim']}")
    if lore.get("killer_hint"):
        parts.append(f"KILLER MOTIVE HINT: {lore['killer_hint']}")
    if lore.get("atmosphere"):
        parts.append(f"ATMOSPHERE: {lore['atmosphere']}")
    char_note = lore.get("character_notes", {}).get(actor_name)
    if char_note:
        parts.append(f"CHARACTER VOICE ({actor_name}): {char_note}")
    room_note = lore.get("room_flavor", {}).get(room_id)
    if room_note:
        parts.append(f"ROOM ({room_id}): {room_note}")
    if object_id:
        conn_note = lore.get("connection_lore", {}).get(object_id)
        if conn_note:
            parts.append(f"WHY THIS OBJECT MATTERS: {conn_note}")
    return "\n".join(parts) if parts else ""


def build_room_entry_prompt(
    *,
    actor_name: str,
    room_id: str,
    room_story: str = "",
    scenario: str,
) -> str:
    """Narrator prompt for the first time a character enters a new room.

    Fires once per room per game. Gives readers an atmospheric establishing shot
    of the space without spoiling its puzzles or contents.
    """
    story_line = f"\nROOM STORY CONTEXT: {room_story}" if room_story else ""
    return (
        f"SCENARIO: {scenario}\n"
        f"CHARACTER: {actor_name} just entered: {room_id.replace('_', ' ')}"
        f"{story_line}\n\n"
        "Write 1-2 sentences describing this room as the character first sees it. "
        "If ROOM STORY CONTEXT is given, let it shape the atmosphere — but do NOT quote or summarize it directly. "
        "Focus on one striking sensory detail: light, smell, sound, or texture. "
        "Simple English. Present tense. Third person. Do not list objects or exits. No em-dashes."
    )


def build_discovery_prompt(
    *,
    actor_name: str,
    item_id: str,
    item_description: str,
    unlocks_description: str,
    connection_lore: str,
    scenario: str,
) -> str:
    """Narrator prompt for a first-discovery beat: connects item to what it unlocks.

    This fires once when a plot-critical item is first touched (taken or inspected).
    The goal is to give readers the 'aha' of WHY this object matters.
    """
    lore_section = (
        f"\nSTORY CONTEXT: {connection_lore}" if connection_lore else ""
    )
    return (
        f"SCENARIO: {scenario}\n"
        f"CHARACTER: {actor_name}\n"
        f"ITEM FOUND: {item_id.replace('_', ' ')} — {item_description}\n"
        f"THIS ITEM ENABLES: {unlocks_description}"
        f"{lore_section}\n\n"
        "Write 1-2 sentences of atmospheric third-person narration for this discovery moment. "
        "Connect the item's physical details to what it enables — give the reader the WHY. "
        "Simple English. Present tense. No invented facts. No em-dashes. "
        "Do NOT state the solution or code directly. Focus on mood and significance."
    )


class WorldSnapshot:
    """Lightweight world state summary for the narrator — grounded, no hallucination."""

    def __init__(
        self,
        *,
        visible_objects: list[str],
        teammate_locations: list[str],
        solved_count: int,
        total_puzzles: int,
    ) -> None:
        self.visible_objects = visible_objects
        self.teammate_locations = teammate_locations
        self.solved_count = solved_count
        self.total_puzzles = total_puzzles

    def render(self) -> str:
        objs = ", ".join(self.visible_objects) or "nothing notable"
        teammates = "; ".join(self.teammate_locations) or "none visible"
        progress = (
            f"{self.solved_count}/{self.total_puzzles} puzzles solved"
            if self.total_puzzles > 0
            else "progress unknown"
        )
        return (
            f"VISIBLE OBJECTS IN ROOM: {objs}\n"
            f"TEAMMATE LOCATIONS: {teammates}\n"
            f"TEAM PROGRESS: {progress}"
        )


def build_dialogue_context_package(
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
    recent_story: list[str],
    lore_excerpt: str = "",
    world_snapshot: "WorldSnapshot | None" = None,
    adapted_world_role: str = "",
    adapted_vocabulary: list[str] | None = None,
    adapted_voice: str = "",
    adapted_sample_lines: list[str] | None = None,
    conversation_seed: str = "",
    proof_revealed: bool = False,
    killer_name: str = "",
    suspects_context: str = "",
) -> str:
    """Build a rigid, low-drift context package for local 7B dialogue models."""
    from app.agents.storyboard import humanize_text
    story = "\n".join(f"- {beat}" for beat in recent_story) or "(session just started)"
    speech_text = humanize_text(speech.strip()) if speech and speech.strip() else "(not stated)"

    lore_section = (
        f"WORLD_LORE (tone/voice reference — do not quote directly):\n{lore_excerpt}\n\n"
        if lore_excerpt else ""
    )
    snapshot_section = (
        f"WORLD_STATE (authoritative — only reference these):\n"
        f"{world_snapshot.render()}\n\n"
        if world_snapshot is not None else ""
    )

    # Use adapted world role from storyboard if available; fall back to raw role+skills.
    if adapted_world_role:
        role_line = f"- ROLE IN THIS WORLD: {adapted_world_role}"
    else:
        skills = ", ".join(actor_skills) if actor_skills else "general"
        role_line = f"- ROLE: {actor_role}\n- SKILLS: {skills}"

    # Vocabulary removed from narrator context — it's already in the player agent's system
    # prompt via apply_storyboard_persona. Exposing it here caused the model to fixate on
    # specific phrases ("the story of this room") and repeat them across every turn.
    # Instead: voice (1 line) + sample_lines (few-shot register anchors). Local models
    # imitate example lines far better than they follow style adjectives, and the
    # "never reuse" rule prevents the catchphrase loop that vocabulary caused.
    voice_line = f"- HOW THEY SPEAK: {adapted_voice}\n" if adapted_voice else ""
    samples_section = ""
    if adapted_sample_lines:
        rendered = "\n".join(f'    "{s}"' for s in adapted_sample_lines[:3])
        samples_section = (
            "- REGISTER EXAMPLES (match this rhythm and attitude — NEVER copy these "
            "lines or their phrases; the situation is always different):\n"
            f"{rendered}\n"
        )

    # Unused conversation seed — a plot-rooted observation to surface if relevant.
    seed_section = (
        f"PLOT_OBSERVATION (surface this naturally if the moment fits):\n"
        f"  {conversation_seed}\n\n"
        if conversation_seed else ""
    )

    # Build investigation status block — mystery mode vs revelation mode.
    if proof_revealed and killer_name:
        investigation_status = (
            "INVESTIGATION_STATUS: REVELATION MODE\n"
            f"The proof has been found. The killer is {killer_name}. "
            f"Characters now know who is responsible. You MAY name {killer_name} explicitly.\n"
        )
    else:
        suspects_block = (
            f"KNOWN SUSPECTS (any could be responsible — do not name which is guilty):\n{suspects_context}\n"
            if suspects_context else
            "KNOWN SUSPECTS: identity unknown.\n"
        )
        investigation_status = (
            "INVESTIGATION_STATUS: MYSTERY MODE\n"
            + suspects_block
            + "FORBIDDEN: naming any specific suspect as the killer. "
            "BANNED PHRASE: 'whoever did this' — talk about the evidence itself instead.\n"
        )

    tension = _tension_level(turn, world_snapshot)
    return (
        "CONTEXT_PACKAGE\n"
        "GLOBAL_CONTEXT:\n"
        f"- SCENARIO: {scenario}\n"
        f"- OBJECTIVE: {objective}\n"
        f"- TURN: {turn} | TENSION_LEVEL: {tension}\n"
        f"STORY_SO_FAR (most recent last — react to the last entry if it's from a teammate):\n{story}\n\n"
        f"{lore_section}"
        f"{seed_section}"
        f"{snapshot_section}"
        f"{investigation_status}\n"
        "CHARACTER_SHEET (shapes this character's specific lens on everything they see):\n"
        f"- NAME: {actor_name}\n"
        + (f"- PRONOUNS: {actor_gender} — use correct pronouns throughout\n" if actor_gender else "")
        + f"{role_line}\n"
        + voice_line
        + samples_section
        + f"- BACKSTORY_HINT: {actor_backstory or 'ordinary survivor under pressure'}\n"
        f"- MOOD: {_mood_for(status, success=success)}\n\n"
        "ACTION_EVENT:\n"
        f"- ATTEMPT: {action_text}\n"
        f"- PLAYER_MOTIVATION (background only — DO NOT echo this as your line; it is what the character is thinking, not what they say):\n"
        f"  {speech_text}\n"
        f"- SIMULATOR_OUTCOME: {outcome}\n"
        f"- SUCCESS: {'yes' if success else 'no'}\n\n"
        "WRITING_TARGET:\n"
        "- FORMAT: Character Name: \"message\"\n"
        "- LENGTH: 10 to 20 words — short, punchy, specific\n"
        "- STEP 1: react to the last STORY_SO_FAR entry if it's a teammate\n"
        "- STEP 2: add what SIMULATOR_OUTCOME just revealed, through CHARACTER_SHEET lens\n"
        "- FORBIDDEN: — or – or -- or rhetorical questions (Why...? What...? Could this...?)\n"
        "- FORBIDDEN: echoing PLAYER_MOTIVATION word-for-word\n"
        "- FORBIDDEN: invented facts not in SIMULATOR_OUTCOME\n"
        "- FORBIDDEN: reusing any distinctive phrase that already appears in STORY_SO_FAR — "
        "if this character said something similar before, find a NEW way to say it\n"
        "Write the single dialogue line now. Stop after the closing quote."
    )


def build_scenario_user_prompt(setting: GameSetting) -> str:
    """Prompt for the very first narration beat: describe the place and context."""
    return (
        "Describe this escape room setting to the reader as an establishing shot.\n\n"
        f"SCENARIO: {setting.scenario}\n\n"
        "Write 2-3 sentences describing the place itself: what it looks like, "
        "sounds like, feels like. Third person. Present tense. No characters yet, "
        "no actions. Just the world the reader is about to enter. No dashes."
    )


def build_opening_user_prompt(setting: GameSetting, storyboard: "Storyboard | None" = None) -> str:
    rooms_text = ", ".join(setting.rooms)

    story_context_parts: list[str] = []
    if storyboard and not storyboard.is_empty():
        if storyboard.plot_victim:
            story_context_parts.append(f"THE VICTIM: {storyboard.plot_victim}")
        if storyboard.plot_protagonist_context:
            story_context_parts.append(f"WHO THEY ARE: {storyboard.plot_protagonist_context}")
        if storyboard.plot_timeline:
            story_context_parts.append(f"WHAT HAPPENED BEFORE: {storyboard.plot_timeline}")
        if storyboard.plot_stakes:
            story_context_parts.append(f"WHAT'S AT STAKE: {storyboard.plot_stakes}")
        if storyboard.plot_atmosphere:
            story_context_parts.append(f"ATMOSPHERE: {storyboard.plot_atmosphere}")

    story_context = (
        "\nSTORY CONTEXT (use specific names and facts from here — do NOT quote directly):\n"
        + "\n".join(story_context_parts)
        + "\n"
    ) if story_context_parts else ""

    return (
        "Write the opening moment: the team arrives and feels the full weight of what they face.\n\n"
        f"SETTING: {setting.scenario}\n"
        f"THEIR MISSION: {setting.objective}\n"
        f"ROOMS THEY WILL SEARCH: {rooms_text}\n"
        + story_context
        + "\nWrite 2-3 sentences. Open with a single sharp sensory detail (smell, sound, or sight). "
        "If STORY CONTEXT includes a victim name, use it — make them real. "
        "End with what's concretely at stake — say 'the killer', 'whoever is responsible', or 'the murderer'. "
        "NEVER name the killer or any suspect in the opening — the mystery must remain unsolved. "
        "Present tense. Third person. No em-dashes. No generic words like 'eerie' or 'dark secrets'."
    )


def build_turn_user_prompt(
    *,
    actor_name: str,
    actor_role: str,
    action_text: str,
    speech: str | None,
    outcome: str,
    success: bool,
    status: ActionExecutionStatus,
    scenario: str,
    objective: str,
    actor_skills: list[str],
    actor_backstory: str,
    actor_gender: str = "",
    turn: int,
    recent_story: list[str],
    lore_excerpt: str = "",
    world_snapshot: "WorldSnapshot | None" = None,
    adapted_world_role: str = "",
    adapted_vocabulary: list[str] | None = None,
    adapted_voice: str = "",
    adapted_sample_lines: list[str] | None = None,
    conversation_seed: str = "",
    proof_revealed: bool = False,
    killer_name: str = "",
    suspects_context: str = "",
) -> str:
    return build_dialogue_context_package(
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
        recent_story=recent_story,
        lore_excerpt=lore_excerpt,
        world_snapshot=world_snapshot,
        adapted_world_role=adapted_world_role,
        adapted_vocabulary=adapted_vocabulary,
        adapted_voice=adapted_voice,
        adapted_sample_lines=adapted_sample_lines,
        conversation_seed=conversation_seed,
        proof_revealed=proof_revealed,
        killer_name=killer_name,
        suspects_context=suspects_context,
    )


class SystemEventKind(str, Enum):
    LOOP_AVOIDED = "loop_avoided"
    PLANNER_OVERRIDE = "planner_override"
    CRITICAL_STUCK = "critical_stuck"
    MILESTONE = "milestone"


def build_system_event_prompt(
    *,
    event_kind: SystemEventKind,
    actor_name: str,
    detail: str,
    scenario: str,
) -> str:
    """Build a narrator prose prompt for internal system events."""
    if event_kind == SystemEventKind.LOOP_AVOIDED:
        instruction = (
            f"{actor_name} just tried to repeat something that already failed or was already done. "
            f"Detail: {detail}\n\n"
            "Narrate this as a brief atmospheric beat: the character's body language, "
            "a flicker of frustration, the dead end. 1-2 sentences."
        )
    elif event_kind == SystemEventKind.PLANNER_OVERRIDE:
        instruction = (
            f"The team was forced onto a different action. Detail: {detail}\n\n"
            "Narrate this as a moment of urgency or course correction. 1-2 sentences."
        )
    elif event_kind == SystemEventKind.CRITICAL_STUCK:
        instruction = (
            f"The team is critically stuck and going in circles. Detail: {detail}\n\n"
            "Narrate rising tension, the clock ticking, desperation creeping in. 1-2 sentences."
        )
    else:  # MILESTONE
        instruction = (
            f"The team just achieved a breakthrough: {detail}\n\n"
            "Narrate the moment of success, the shift in energy. 1-2 sentences."
        )

    return (
        f"SCENARIO: {scenario}\n\n"
        f"EVENT: {instruction}\n\n"
        "Write the narration now. No invented facts. No dashes. Present tense."
    )


def build_ending_user_prompt(
    setting: GameSetting,
    won: bool,
    *,
    ending_guidance: str = "",
    wrong_deduction: bool = False,
    killer_name: str = "",
    victim: str = "",
    motive: str = "",
    proof_sentence: str = "",
) -> str:
    guidance_section = (
        f"\nSTORY ENDING GUIDANCE (tone reference):\n{ending_guidance}\n"
        if ending_guidance else ""
    )
    case_facts = ""
    if killer_name:
        case_facts = "\nCASE FACTS (authoritative — use these, do not invent):\n"
        case_facts += f"- THE KILLER: {killer_name}\n"
        if victim:
            case_facts += f"- THE VICTIM: {victim}\n"
        if motive:
            case_facts += f"- THE MOTIVE: {motive}\n"
        if proof_sentence:
            case_facts += f"- THE PROOF: {proof_sentence}\n"

    if won:
        return (
            f"Conclude the murder mystery. The team SOLVED it — they named {killer_name or 'the killer'} correctly.\n"
            f"{case_facts}{guidance_section}\n"
            "Write the CONFRONTATION SCENE in 4-6 sentences, following these beats IN ORDER:\n"
            f"1. THE ACCUSATION: the team names {killer_name or 'the killer'} to their face. "
            "One breath where denial seems possible.\n"
            "2. THE PROOF: the specific evidence is laid out — the denial dies. "
            "Use THE PROOF from CASE FACTS.\n"
            "3. THE MOTIVE: the why finally spills out — use THE MOTIVE from CASE FACTS. "
            "Let the killer's composure crack as it surfaces.\n"
            f"4. RESOLUTION: {killer_name or 'the killer'} is CAUGHT — they do NOT escape, "
            "do NOT walk into the night. The victim is acknowledged in the final line.\n"
            "HARD RULE: the killer does not get away. No fading footsteps, no slammed doors behind them.\n"
            "No new puzzle details. No em-dashes."
        )
    if wrong_deduction:
        return (
            f"Conclude the murder mystery. The team accused the WRONG person — "
            f"the real killer{f', {killer_name},' if killer_name else ''} walks free.\n"
            f"{case_facts}{guidance_section}\n"
            "Write 3-4 sentences: the accusation falls apart, the real killer slips away "
            "while everyone looks the wrong way, and the truth surfaces one beat too late. "
            "End on the cost of the mistake. No em-dashes."
        )
    return (
        f"Conclude the murder mystery. The team RAN OUT OF TIME — the case is unsolved.\n"
        f"{case_facts}{guidance_section}\n"
        "Write 3-4 sentences of grim, unresolved close: the investigation is sealed, "
        "the evidence goes cold, and whoever did it remains unnamed. "
        "Do NOT reveal the killer's name in this ending. No em-dashes."
    )
