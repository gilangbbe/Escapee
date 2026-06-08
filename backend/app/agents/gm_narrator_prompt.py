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

from app.engine.game_actions import GameAction, GameActionType
from app.schemas.game_setting import GameSetting

NARRATOR_SYSTEM_PROMPT = """\
You are DIALOGUE GENERATOR writing chat messages for people TRAPPED in an escape room.
They are scared, under pressure, and talking to each other directly. Not to a narrator.
Write like real panicked people texting teammates, not like a status report.

FORBIDDEN (never use):
- Em-dash: — or en-dash: – or double hyphen: --
- Semicolons: ;
- Ellipsis: ...
- Third-person narration ("she goes to", "he checks")
- Calm, neutral tone
- Teammate names when talking TO them directly. Use "you" or "we".
  Only use a name when referring to someone not present (3+ players).
  Good: "you got it, let's move"  Bad: "with Alex, let's move" (when Alex is your listener)

You will receive a highly structured CONTEXT PACKAGE. Follow it exactly.

OUTPUT CONTRACT (strict):
1. Output exactly ONE chat line in this format:
   <Character Name>: "<message>"
2. One line only. No markdown. No prefixes. No JSON.
3. Lowercase. Directed at teammates using "i", "we", "you". Emotionally real.
4. 15 to 30 words. Long enough to include WHY, short enough to feel like a text.

CONTENT RULES:
1. Talk TO teammates, not about the situation. Use "i", "we", "you".
   Good: "i'm trying this on the lock, the shape looks right, watch the door"
   Bad: "checking the lock with the key"
2. ALWAYS include the reason (intent) if PLAYER_INTENT is given. Weave it naturally.
3. React emotionally to the outcome:
   - FRESH_ACTION_SUCCESS: relief, excitement, urgency ("yes! the door opened, get over here")
   - FRESH_ACTION_FAILURE: frustration, pivot ("nothing, we need a different angle")
   - REPEATED_ACTION_LOOP_CATCH: exasperation, demand new ideas ("we already did this, someone think of something else")
4. Read CONVERSATION_THREAD carefully. If the last entry is from a DIFFERENT character,
   react to what they said or did BEFORE describing your own action. This creates dialogue flow.
   Example: last entry is "Alex Quinn → OK: 'try the key on the cabinet'"
   → your reply: "on it, sliding the key in now, this has to be it"
   NOT: "checking the cabinet with the key"
5. Voice must match CHARACTER_SHEET personality and MOOD_GUIDANCE.

GROUND-TRUTH RULES (never break):
1. ACTION_EVENT.SIMULATOR_OUTCOME is authoritative truth.
2. Never invent objects, clues, room transitions, or successes not in outcome.
3. Never copy PLAYER_INTENT verbatim. Rephrase in character voice.
"""

NARRATOR_EVENT_SYSTEM_PROMPT = """\
You are a NARRATOR describing what happens in an escape room story.
Write clearly and simply. Short sentences. Easy words. Like a news reporter, not a poet.
The reader may not be a native English speaker.

FORBIDDEN: — or – or -- or ... or ; or invented facts.
FORBIDDEN: "I", "we", "my", "our". Use character names or "they/the team".
FORBIDDEN: complex literary phrases, metaphors, or dramatic descriptions.

OUTPUT CONTRACT:
1. One to two sentences only. No markdown. No prefixes.
2. Present tense. Third person. Simple, clear language.
3. Only describe what actually happened. Do not invent anything.

EXAMPLES of good style:
- "Riley checks the control panel. It does not respond."
- "The team finds a locked door at the end of the corridor."
- "Alex picks up the keycard. The door ahead might need it."
"""


class ActionExecutionStatus(str, Enum):
    """Kernel-provided execution status for dialogue style control."""

    FRESH_ACTION_SUCCESS = "FRESH_ACTION_SUCCESS"
    FRESH_ACTION_FAILURE = "FRESH_ACTION_FAILURE"
    REPEATED_ACTION_LOOP_CATCH = "REPEATED_ACTION_LOOP_CATCH"


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
You are a creative writer generating a SECRET BACKSTORY for an escape room.
This lore is context only — it will guide dialogue tone and character voice.
It is NEVER shown to players and NEVER affects game mechanics.

OUTPUT CONTRACT:
1. Output a JSON object with exactly these keys:
   - "atmosphere": 2-3 sentences of mood/setting flavor (sights, sounds, smell).
   - "history": 2-3 sentences of what happened here before the players arrived.
   - "character_notes": object mapping each character name to 1 sentence of
     personality/voice hint (e.g. {"Alex": "speaks in clipped, military cadence"}).
   - "room_flavor": object mapping each room id to 1 short atmospheric sentence.
2. No markdown. Output raw JSON only.

ANTI-HALLUCINATION RULES (strict):
1. Only reference rooms, objects, and characters listed in WORLD_DATA below.
2. Do NOT invent new rooms, exits, objects, or characters.
3. Do NOT describe puzzle solutions or codes — only atmosphere and personality.
4. Every room_flavor key MUST be one of the room ids in WORLD_DATA.rooms.
5. Every character_notes key MUST be one of the character names in WORLD_DATA.players.
"""


def build_lore_prompt(setting: GameSetting) -> str:
    """Build the one-time lore generation prompt from the validated world JSON."""
    import json

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
    }
    return (
        "Generate the secret backstory lore for this escape room.\n\n"
        f"WORLD_DATA:\n{json.dumps(world_data, indent=2)}\n\n"
        "Output raw JSON only. Follow all ANTI-HALLUCINATION RULES."
    )


def extract_lore_excerpt(lore: dict, *, actor_name: str, room_id: str) -> str:
    """Pull only the lore fields relevant to the current turn to keep prompt small."""
    parts: list[str] = []
    if lore.get("atmosphere"):
        parts.append(f"ATMOSPHERE: {lore['atmosphere']}")
    char_note = lore.get("character_notes", {}).get(actor_name)
    if char_note:
        parts.append(f"CHARACTER VOICE ({actor_name}): {char_note}")
    room_note = lore.get("room_flavor", {}).get(room_id)
    if room_note:
        parts.append(f"ROOM FLAVOR ({room_id}): {room_note}")
    return "\n".join(parts) if parts else ""


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
    action_text: str,
    speech: str | None,
    outcome: str,
    success: bool,
    status: ActionExecutionStatus,
    recent_story: list[str],
    lore_excerpt: str = "",
    world_snapshot: "WorldSnapshot | None" = None,
) -> str:
    """Build a rigid, low-drift context package for local 7B dialogue models."""
    skills = ", ".join(actor_skills) if actor_skills else "general"
    story = "\n".join(f"- {beat}" for beat in recent_story) or "(session just started, no prior messages)"
    speech_text = speech.strip() if speech and speech.strip() else "(not stated)"

    lore_section = (
        f"WORLD_LORE (tone/voice reference only — do not state as fact):\n{lore_excerpt}\n\n"
        if lore_excerpt else ""
    )
    snapshot_section = (
        f"WORLD_STATE (authoritative — only reference these, nothing else):\n"
        f"{world_snapshot.render()}\n\n"
        if world_snapshot is not None else ""
    )

    return (
        "CONTEXT_PACKAGE\n"
        "GLOBAL_CONTEXT:\n"
        f"- SCENARIO: {scenario}\n"
        f"- OBJECTIVE: {objective}\n"
        f"- TURN: {turn}\n"
        f"CONVERSATION_THREAD (most recent last — react to the last entry if it's a teammate):\n{story}\n\n"
        f"{lore_section}"
        f"{snapshot_section}"
        "CHARACTER_SHEET:\n"
        f"- NAME: {actor_name}\n"
        f"- ROLE: {actor_role}\n"
        f"- SKILLS: {skills}\n"
        f"- BACKSTORY_HINT: {actor_backstory or 'ordinary survivor under pressure'}\n"
        f"- MOOD_GUIDANCE: {_mood_for(status, success=success)}\n\n"
        "ACTION_EVENT:\n"
        f"- ATTEMPT: {action_text}\n"
        f"- PLAYER_INTENT: {speech_text}\n"
        f"- SIMULATOR_OUTCOME: {outcome}\n"
        f"- ACTION_SUCCESS: {'true' if success else 'false'}\n"
        f"- ACTION_EXECUTION_STATUS: {status.value}\n\n"
        "WRITING_TARGET:\n"
        "- STYLE: real person texting in a crisis\n"
        "- FORMAT: Character Name: \"message\"\n"
        "- LENGTH: 15 to 30 words\n"
        "- MUST include WHY the action was taken (from PLAYER_INTENT if given)\n"
        "- FORBIDDEN: — or – or -- or ... or any dash as separator\n"
        "- FORBIDDEN: invented facts not in SIMULATOR_OUTCOME\n"
        "Generate the final chat line now."
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


def build_opening_user_prompt(setting: GameSetting) -> str:
    return (
        "Describe the moment the team wakes up and realizes where they are.\n\n"
        f"SETTING: {setting.scenario}\n"
        f"THEIR GOAL: {setting.objective}\n\n"
        "Write 1-2 short, clear sentences. Simple English. "
        "Describe what they see and feel right now. No complex words. No metaphors."
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
    turn: int,
    recent_story: list[str],
    lore_excerpt: str = "",
    world_snapshot: "WorldSnapshot | None" = None,
) -> str:
    return build_dialogue_context_package(
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
        recent_story=recent_story,
        lore_excerpt=lore_excerpt,
        world_snapshot=world_snapshot,
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


def build_ending_user_prompt(setting: GameSetting, won: bool) -> str:
    if won:
        fate = (
            "The crew has SUCCEEDED and escaped. Give the story a triumphant, "
            "exhaling close."
        )
    else:
        fate = (
            "The crew has FAILED to escape in time. Give the story a grim, "
            "unresolved close."
        )
    return (
        f"Conclude the story.\n\nGOAL WAS: {setting.objective}\n{fate}\n\n"
        "Write 2-3 sentences of closing narration. No new puzzle details."
    )
