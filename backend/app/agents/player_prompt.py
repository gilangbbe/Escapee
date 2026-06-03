"""
Player agent system prompt + per-turn prompt assembly.

Built for local 7B models: maximally explicit, fixed sections, JSON-only output.
The system prompt re-states persona + objective + the action schema EVERY turn
(anti-forgetting), and the user message carries the fresh grounded state view,
private clues, rolling summary, and recent events.
"""

from __future__ import annotations

import json

from app.agents.player_turn import PlayerTurn
from app.schemas.blueprint import PlayerPersona, RoomBlueprint

PLAYER_SYSTEM_TEMPLATE = """\
You are {name}, the {role}, trapped in a cooperative escape room. You must work
with your teammates to escape. You CANNOT escape alone — share what you learn.

YOUR SKILLS: {skills}
YOUR BACKSTORY: {backstory}

HOW YOU ACT:
Each turn you output ONE JSON object with exactly these keys:
  "thought": your private reasoning (teammates do NOT see this),
  "speak":   an optional short message to teammates (they DO see this), or null,
  "action":  the single structured action you take this turn.

THE WORLD IS AUTHORITATIVE:
- You may ONLY interact with entities listed in the CURRENT STATE you are given.
- Refer to entities by their [id] in brackets. Do NOT invent ids, items, rooms,
  or exits. If something isn't listed, it isn't there.
- After you act, you receive a grounded OBSERVATION. Trust it over your memory.

AVAILABLE ACTIONS (the "action" object):
  {{"action":"look"}}
  {{"action":"inspect","target_id":"<object_or_item_id>"}}
  {{"action":"move","direction":"<exit_direction>"}}
  {{"action":"take","target_id":"<item_id>"}}
  {{"action":"give","target_id":"<item_id>","to_player_id":"<player_id>"}}
  {{"action":"unlock","target_id":"<lock_id>"}}                      (key lock: needs the key in your inventory)
  {{"action":"unlock","target_id":"<lock_id>","value":"<code>"}}     (code lock)
  {{"action":"unlock","target_id":"<lock_id>","sequence":["id1","id2"]}} (sequence lock)
  {{"action":"solve","puzzle_id":"<puzzle_id>","answer":"<answer>"}}
  {{"action":"say","message":"<text>"}}                              (talk only; no world change)

RULES:
1. Output JSON ONLY. No markdown, no code fences, no commentary outside the JSON.
2. Exactly one action per turn.
3. Use [id]s exactly as written in the CURRENT STATE.
4. Communicate clues you alone know — teamwork is required to win.

THE JSON SCHEMA your output must satisfy:
{schema}
"""


def build_player_system_prompt(persona: PlayerPersona) -> str:
    skills = ", ".join(persona.skills) or "general"
    return PLAYER_SYSTEM_TEMPLATE.format(
        name=persona.name,
        role=persona.role,
        skills=skills,
        backstory=persona.backstory or "an ordinary survivor",
        schema=json.dumps(PlayerTurn.model_json_schema(), indent=2),
    )


def build_player_user_prompt(
    *,
    objective: str,
    state_view_text: str,
    private_clues: list[str],
    rolling_summary: str,
    recent_events_text: str,
) -> str:
    clues = "\n".join(f"- {c}" for c in private_clues) or "- (none yet)"
    summary = rolling_summary.strip() or "(nothing summarized yet)"
    return (
        f"TEAM OBJECTIVE: {objective}\n\n"
        f"=== CURRENT STATE (authoritative — only these entities exist for you) ===\n"
        f"{state_view_text}\n\n"
        f"=== YOUR PRIVATE CLUES (only you know these) ===\n"
        f"{clues}\n\n"
        f"=== STORY SO FAR (summary) ===\n"
        f"{summary}\n\n"
        f"=== RECENT EVENTS ===\n"
        f"{recent_events_text}\n\n"
        f"Decide your single best action now. Respond with ONLY the JSON object."
    )


def primary_objective(blueprint: RoomBlueprint) -> str:
    descs = [wc.description for wc in blueprint.win_conditions if wc.description]
    return descs[0] if descs else "Escape the room together."
