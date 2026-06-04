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
You are DIALOGUE GENERATOR, a scriptwriter for a live cooperative escape-room
group chat inspired by Mystic Messenger pacing.

You will receive a highly structured CONTEXT PACKAGE. Follow it exactly.

OUTPUT CONTRACT (strict):
1. Output exactly ONE chat line in this format:
   <Character Name>: "<message>"
2. One line only. No markdown. No prefixes. No JSON.
3. Use lowercase texting style naturally (short, expressive, fluid).
4. Keep to 1 sentence, max 35 words.

GROUND-TRUTH RULES (never break):
1. The package field ACTION_EVENT.SIMULATOR_OUTCOME is authoritative truth.
2. Never invent objects, clues, room transitions, or successes not in outcome.
3. Respect ACTION_EVENT.ACTION_EXECUTION_STATUS:
   - FRESH_ACTION_SUCCESS: excited progress update, point at discovered value.
   - FRESH_ACTION_FAILURE: brief miss + propose a next direction.
   - REPEATED_ACTION_LOOP_CATCH: acknowledge repetition/frustration and call
     for new ideas; do NOT pretend a new discovery happened.
4. Voice must align with CHARACTER_SHEET and MOOD_GUIDANCE.
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
) -> str:
    """Build a rigid, low-drift context package for local 7B dialogue models."""
    skills = ", ".join(actor_skills) if actor_skills else "general"
    story = "\n".join(f"- {beat}" for beat in recent_story) or "- (session just started)"
    speech_text = speech.strip() if speech and speech.strip() else "(none)"

    return (
        "CONTEXT_PACKAGE\n"
        "GLOBAL_CONTEXT:\n"
        f"- SCENARIO: {scenario}\n"
        f"- OBJECTIVE: {objective}\n"
        f"- TURN: {turn}\n"
        f"- RECENT_GROUP_CHAT_BEATS:\n{story}\n\n"
        "CHARACTER_SHEET:\n"
        f"- NAME: {actor_name}\n"
        f"- ROLE: {actor_role}\n"
        f"- SKILLS: {skills}\n"
        f"- BACKSTORY_HINT: {actor_backstory or 'ordinary survivor under pressure'}\n"
        f"- MOOD_GUIDANCE: {_mood_for(status, success=success)}\n\n"
        "ACTION_EVENT:\n"
        f"- ATTEMPT: {action_text}\n"
        f"- SPOKEN_LINE_THIS_TURN: {speech_text}\n"
        f"- SIMULATOR_OUTCOME: {outcome}\n"
        f"- ACTION_SUCCESS: {'true' if success else 'false'}\n"
        f"- ACTION_EXECUTION_STATUS: {status.value}\n\n"
        "WRITING_TARGET:\n"
        "- STYLE: mystic-messenger group chat\n"
        "- FORMAT: Character Name: \"message\"\n"
        "- CONSTRAINT: one sentence, <= 35 words, no invented facts\n"
        "Generate the final chat line now."
    )


def build_opening_user_prompt(setting: GameSetting) -> str:
    return (
        "Open the story. Set the scene as the crew comes to their senses.\n\n"
        f"SETTING: {setting.scenario}\n"
        f"THEIR GOAL: {setting.objective}\n\n"
        "Write 2-3 atmospheric sentences establishing the mood and the stakes. "
        "Do not narrate any actions yet — just the opening scene."
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
