"""
Player agent prompt assembly (v2 — GameSetting engine).

Built for local 7B models: maximally explicit, fixed sections, JSON-only output.
The system prompt re-states persona + the action grammar EVERY turn
(anti-forgetting); the user message carries the fresh grounded state view,
private clues, rolling summary, and recent events.
"""

from __future__ import annotations

import json

from app.agents.game_turn import GameTurn, PlanProposal
from app.context.game_builder import TeamBrief
from app.schemas.game_setting import GameSetting, PlayerPersona

PLAYER_SYSTEM_TEMPLATE = """\
You are {name}, the {role}, trapped in a cooperative escape room. You must work
with your teammates to escape. You CANNOT escape alone — think out loud as a
team, share what you learn, and agree on a plan before committing.

YOUR SKILLS: {skills}
YOUR BACKSTORY: {backstory}

HOW YOU ACT:
Each turn you produce ONE structured turn with these parts:
  "reflection": OPTIONAL summary of what's known / what failed / the best next
                plan — fill this on REFLECTION checkpoints (teammates see it),
  "hypothesis": OPTIONAL public theory about the puzzle + the next step you
                propose the team try (teammates DO see this),
  "speak":      OPTIONAL short message to teammates — use it to SUPPORT,
                CRITIQUE, or COUNTER a teammate's idea, or share an
                observation (teammates DO see this),
  "action":     the single structured action you take this turn.
The output format is enforced for you — just choose good content; you do not
need to worry about JSON syntax, quoting, or commas.

THE WORLD IS AUTHORITATIVE:
- You may ONLY interact with entities listed in the CURRENT STATE you are given.
- In the state view, ids are shown wrapped in [brackets] for readability. The
  brackets are NOT part of the id. Write the BARE id WITHOUT brackets. Example:
  the state shows [captains_log] -> set target_id to captains_log (never
  "[captains_log]").
- Do NOT invent ids, objects, rooms, or exits. If something isn't listed, it
  isn't there.
- After you act, you receive a grounded OBSERVATION. Trust it over your memory.

AVAILABLE ACTIONS (set "action" to the verb, fill ONLY the fields it needs):
  look                                  - look around the room
  inspect    (target_id)                - examine an object
  take       (target_id)                - pick up a takeable object
  enter_code (target_id, code)          - enter a code on an object
  use        (item_id, target_id)       - use a held item on an object
  set_fuse   (target_id, fuse, position)- flip a fuse ON/OFF on a panel
  move       (to_room)                  - move through an OPEN exit only
  give       (item_id, to_player_id)    - hand an item to a player in the room
  say        (message)                  - talk only; no world change

HIGH-IMPACT DIRECTIVES:
1. FOLLOW THE RECOMMENDATION: if a "⭐ RECOMMENDED NEXT ACTION" is given, DO
    EXACTLY THAT unless you can state a concrete reason a different CANDIDATE is
    clearly better. It is computed from the real world state and is almost always
    the fastest progress.
2. PROGRESS OVER LOOKING: prefer actions that change the world (take / use /
    enter_code / set_fuse / move) over inspect/say. Never inspect an object listed
    under ALREADY EXAMINED — it reveals nothing new.
3. PICK A CANDIDATE: choose from the CANDIDATE ACTIONS you are given; they are
    pre-validated as reachable and useful. An action outside that list will be
    rejected and you will be asked to decide again.
4. NO REDUNDANCY: do not repeat solved goals or listed failed actions unless the
    world changed.
5. COORDINATE: if a teammate is in another room and the next step is there, MOVE
    to join them (or pass them an item). Share new clues/blockers immediately.
"""



def build_player_system_prompt(persona: PlayerPersona) -> str:
    skills = ", ".join(persona.skills) or "general"
    return PLAYER_SYSTEM_TEMPLATE.format(
        name=persona.name,
        role=persona.role,
        skills=skills,
        backstory=persona.backstory or "an ordinary survivor",
    )


def build_player_user_prompt(
    *,
    objective: str,
    state_view_text: str,
    private_clues: list[str],
    team_brief: TeamBrief | None = None,
) -> str:
    clues = "\n".join(f"- {c}" for c in private_clues) or "- (none yet)"
    team_section = (
        team_brief.render()
        if team_brief is not None
        else "CURRENT GOAL: (none)\nBLOCKERS: (none)\nDO NOT REPEAT: (none yet)"
    )
    stuck = team_brief is not None and team_brief.stuck
    reflect = team_brief is not None and team_brief.reflect
    blocked = team_brief is not None and bool(team_brief.blocked_note)

    if blocked:
        closing = (
            "Your previous choice was BLOCKED as a proven dead-end (see ⛔ above). "
            "Pick a genuinely DIFFERENT action — explore an UNEXPLORED object, apply "
            "a clue you haven't used, move through an open exit, or hand off to a "
            "teammate. Respond with ONLY the JSON object."
        )
    elif reflect:
        closing = (
            "This is a REFLECTION checkpoint. Fill \"reflection\" with a short recap of "
            "what is known, what has FAILED, and the single best next plan — then take "
            "the action that plan implies (not a repeat). Respond with ONLY the JSON object."
        )
    elif stuck:
        closing = (
            "The team is stuck — do NOT repeat a blocked or already-tried action. "
            "Read the OBJECTIVE BOARD, pick the NEXT UNSOLVED prerequisite, propose "
            "it in \"hypothesis\", react to your teammates in \"speak\", and take "
            "that different action now. Respond with ONLY the JSON object."
        )
    else:
        closing = (
            "Decide your single best NEW action now. If a ⭐ RECOMMENDED NEXT ACTION "
            "is shown, execute exactly that (you may voice a short reaction in "
            "\"speak\"); otherwise pick the highest-progress CANDIDATE. Never redo "
            "anything under SOLVED or re-inspect an ALREADY EXAMINED object. Respond "
            "with ONLY the JSON object."
        )
    return (
        f"TEAM OBJECTIVE: {objective}\n\n"
        f"=== CURRENT STATE ===\n"
        f"{state_view_text}\n\n"
        f"=== PRIVATE KNOWLEDGE ===\n"
        f"{clues}\n\n"
        f"=== TEAM MEMORY / GOAL / BLOCKERS ===\n"
        f"{team_section}\n\n"
        f"{closing}"
    )




def primary_objective(setting: GameSetting) -> str:
    return setting.objective or "Escape the room together."


PLAN_SYSTEM_TEMPLATE = """\
You are {name}, the {role}. Before your team starts acting, you plan together.
Your job right now is to think about the OBJECTIVE and produce a clear,
ordered, step-by-step escape plan the whole team will follow.

YOUR SKILLS: {skills}

RULES:
- Produce a private "thought" and an ordered "plan" list of steps. The output
  format is enforced for you — focus on good, ordered content.
- Each step is a short, concrete action ("inspect the captain's log for the
  locker code", "enter the code on the supply locker", "use the power cell on
  the panel", "move to the command deck", "unlock the airlock").
- Order the steps the way they must actually happen (clues before the locks they
  open; items taken before they are used; power restored before powered doors).
- Use ONLY the objects/rooms named in the briefing. Do NOT invent entities.
- 4 to 8 steps. No markdown, no commentary.
"""


def build_plan_system_prompt(persona: PlayerPersona) -> str:
    skills = ", ".join(persona.skills) or "general"
    return PLAN_SYSTEM_TEMPLATE.format(
        name=persona.name, role=persona.role, skills=skills
    )


def build_plan_user_prompt(
    *,
    objective: str,
    state_view_text: str,
    private_clues: list[str],
    open_puzzles: list[str],
    draft_plan: list[str] | None = None,
) -> str:
    clues = "\n".join(f"- {c}" for c in private_clues) or "- (none)"
    puzzles = "\n".join(f"- {p}" for p in open_puzzles) or "- (none listed)"
    if draft_plan:
        draft = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(draft_plan))
        task = (
            "A teammate drafted the plan below. CRITIQUE and IMPROVE it: fix wrong "
            "ordering, add any missing step, remove redundant ones, and fold in what "
            "only YOU know from your private clues. Return the FULL improved plan.\n\n"
            f"DRAFT PLAN:\n{draft}\n"
        )
    else:
        task = (
            "Propose the team's step-by-step escape plan from scratch, using what you "
            "can see plus your private clues.\n"
        )
    return (
        f"TEAM OBJECTIVE: {objective}\n\n"
        f"=== WHAT YOU CAN SEE NOW ===\n{state_view_text}\n\n"
        f"=== YOUR PRIVATE CLUES (only you know these — bake them into the plan) ===\n"
        f"{clues}\n\n"
        f"=== OPEN PUZZLES (what still needs solving) ===\n{puzzles}\n\n"
        f"{task}\n"
        f"Provide your private thought and the ordered plan steps."
    )

