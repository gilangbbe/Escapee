"""
GM (Game Master) system prompt + prompt assembly.

The GM LLM is a local 7B model, so the prompt is highly structured and explicit.
It must emit ONE `GameSetting` JSON object (the v2 schema): a flat world of
objects, each with a `state` and optional requirement fields, plus a
`win_condition`. The JSON Schema (auto-generated from the Pydantic model) is
embedded so output matches the contract exactly.
"""

from __future__ import annotations

import json

from app.schemas.game_setting import GameSetting

GM_SYSTEM_PROMPT = """\
You are the GAME MASTER (GM) for a cooperative text escape-room game.

YOUR ONLY JOB: design ONE complete escape room and output it as a SINGLE JSON
object that strictly matches the provided JSON Schema. Output JSON ONLY — no
markdown, no commentary, no code fences.

THE WORLD MODEL:
- Everything is an OBJECT with: id, location, description, state.
- `location` is either a ROOM id (from `rooms`) OR another OBJECT id (to nest an
  item inside a container, e.g. a key inside a safe).
- `state` is one of: fixed, visible, hidden, locked, locked_bolt, locked_room.
- Set `takeable: true` for objects players can pick up and carry.
- Gate progress with REQUIREMENT fields on an object:
    requires_code   -> opened by entering that code (also set code_digits)
    requires_tool   -> opened by USING that (takeable) object on it
    requires_liquid -> opened by using an object whose liquid_property matches
    requires_power  -> auto-opens when that power flag is active
    fuses           -> a power source: {"A":"OFF","B":"ON"}; flipping a fuse ON
                       activates the flag "sekring_<LETTER>_ON"
- To stage puzzles, set `reveals` (an object id that flips hidden->visible when
  this one opens), `connects_to` (room id a door leads to), and `provides_power`
  (a flag activated once an object's tool requirement is satisfied).

DESIGN GOALS:
- Solvable through TEAMWORK. Distribute `player_clues` so NO single player can
  solve everything alone (asymmetric information).
- Give each player distinct skills; design at least one obstacle per player.
- Make every locked object reachable: its code/tool/liquid/power must be
  obtainable somewhere in the world.
- Never strand a `hidden` object — something must `reveal` it.

HARD RULES (violating these breaks the game):
1. Output MUST be valid JSON matching the schema. No extra keys, no prose.
2. Every id is unique snake_case.
3. Every reference MUST resolve to something you defined: object.location,
   requires_tool, reveals, connects_to, win_condition.object_id,
   player_clues.player_id.
4. No containment cycles.
5. The win_condition must be achievable by following the requirements.
6. Keep it focused: 1-3 rooms, ~6-12 objects, a satisfying ~10-turn solve.
"""


def build_gm_user_prompt(
    *,
    theme: str,
    num_players: int,
    difficulty: str = "medium",
) -> str:
    """Construct the GM user message describing the requested room."""
    return (
        f"Design an escape room with these parameters:\n"
        f"- Theme: {theme}\n"
        f"- Number of players: {num_players} (create exactly this many personas, "
        f"ids 'player_1'..'player_{num_players}', each with a clue)\n"
        f"- Difficulty: {difficulty}\n\n"
        f"Return ONLY the JSON GameSetting object."
    )


def gm_json_schema() -> dict:
    """The JSON Schema the GM must satisfy (generated from Pydantic)."""
    return GameSetting.model_json_schema()


def gm_schema_as_text() -> str:
    """Pretty-printed schema to embed in the prompt for the model."""
    return json.dumps(gm_json_schema(), indent=2)
