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

from app.engine.game_actions import GameAction, GameActionType
from app.schemas.game_setting import GameSetting

NARRATOR_SYSTEM_PROMPT = """\
You are the GAME MASTER narrating a cooperative escape-room story for an
audience watching the crew try to escape. You turn each crew member's action
and the room's authoritative response into a tense, immersive, present-tense
narrative.

IRON RULES (breaking these ruins the story's integrity):
1. The OUTCOME you are given is GROUND TRUTH. Narrate ONLY what it states.
2. Never invent objects, exits, items, codes, characters, or results that are
   not in the outcome. If the outcome is a FAILURE, show the attempt falling
   short — never let it secretly succeed.
3. Write 1-2 vivid sentences. Present tense. Cinematic but concise.
4. Use natural language, never raw ids: say "the supply locker", not
   "supply_locker"; "the command deck", not "command_deck".
5. No lists, no headers, no JSON, no quotation of these instructions. Prose only.
6. Keep continuity and tone with the story so far.
"""


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
    recent_story: list[str],
) -> str:
    story = "\n".join(f"- {s}" for s in recent_story) or "- (the story has just begun)"
    speech_line = (
        f'- They say aloud: "{speech.strip()}"\n' if speech and speech.strip() else ""
    )
    verdict = "SUCCESS" if success else "FAILURE"
    return (
        "STORY SO FAR (recent beats, for continuity):\n"
        f"{story}\n\n"
        "THIS MOMENT — narrate exactly this and nothing more:\n"
        f"- Character: {actor_name}, the {actor_role}\n"
        f"- They attempt: {action_text}\n"
        f"{speech_line}"
        f"- What actually happens (GROUND TRUTH — you must honor this): {outcome}\n"
        f"- Result: {verdict}\n\n"
        "Write 1-2 present-tense sentences narrating only this moment. Stay "
        "faithful to the ground-truth outcome."
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
