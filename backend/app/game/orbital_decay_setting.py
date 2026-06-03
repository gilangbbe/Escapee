"""
"Orbital Decay" ported to the new GameSetting (v2) schema.

This is the canonical, fully-playable cooperative escape room expressed in the
flat object/state/requirement model. Engine-resolution hints (reveals,
connects_to, provides_power) are populated so the deterministic simulator can
run it end-to-end.

Solution path (needs BOTH players' private clues):
  1. Vega inspects captains_log (inside the open cryo_pod) -> learns code 0451.
  2. enter_code supply_locker 0451 -> locker opens, access_card reachable.
  3. take access_card.
  4. use access_card on command_door -> door unlocks -> command_deck accessible.
  5. (both move to command_deck)
  6. Rourke: enter_code nav_console "reroute auxiliary" -> reveals power_cell.
  7. take power_cell.
  8. use power_cell on power_panel -> activates power flag 'airlock_power_ON'.
  9. airlock_door (requires_power 'airlock_power_ON') auto-unlocks -> WIN.

Asymmetric information:
  - player_1 (Vega) alone knows the locker code comes from the captain's log.
  - player_2 (Rourke) alone knows the reroute phrase + the power-cell -> panel chain.
"""

from __future__ import annotations

ORBITAL_DECAY_SETTING: dict = {
    "scenario": (
        "Emergency lighting bathes a derelict orbital research station in red. "
        "Life support is failing; the oxygen reserve reads 18 minutes. Two "
        "survivors wake from cryo-sleep in a sealed bay. Nothing works without "
        "power, and everything is locked down."
    ),
    "objective": "Restore power and unlock the airlock to escape the station.",
    "rooms": ["cryo_bay", "command_deck", "airlock"],
    "start_room": "cryo_bay",
    "objects": [
        # --- Cryo Bay ---
        {
            "id": "cryo_pod",
            "location": "cryo_bay",
            "description": "A cracked cryo-pod, long cold. A data-slate logbook rests inside.",
            "state": "visible",
            "interactable": True,
            "takeable": False,
        },
        {
            "id": "captains_log",
            "location": "cryo_pod",
            "description": (
                "The captain's final entry: 'Sealed our gear behind the old launch "
                "code — zero-four-five-one. If you're reading this, I didn't make it.'"
            ),
            "state": "visible",
            "interactable": True,
            "takeable": False,
            "contains_info": "locker_code_0451",
        },
        {
            "id": "supply_locker",
            "location": "cryo_bay",
            "description": "A reinforced locker secured by a four-digit keypad. Something rattles inside.",
            "state": "locked",
            "interactable": True,
            "takeable": False,
            "requires_code": "0451",
            "code_digits": 4,
        },
        {
            "id": "access_card",
            "location": "supply_locker",
            "description": "A magnetic keycard stamped 'COMMAND DECK — AUTHORIZED ONLY'.",
            "state": "visible",
            "interactable": True,
            "takeable": True,
        },
        {
            "id": "command_door",
            "location": "cryo_bay",
            "description": (
                "A sealed hatch to the command deck. A card reader glows beside it."
            ),
            "state": "locked_bolt",
            "interactable": True,
            "takeable": False,
            "requires_tool": "access_card",
            "connects_to": "command_deck",
        },
        # --- Command Deck ---
        {
            "id": "nav_console",
            "location": "command_deck",
            "description": (
                "A navigation console. An auxiliary power feed can be rerouted by "
                "someone who knows the override phrase."
            ),
            "state": "visible",
            "interactable": True,
            "takeable": False,
            "requires_code": "reroute auxiliary",
            "reveals": "power_cell",
        },
        {
            "id": "power_cell",
            "location": "command_deck",
            "description": "A charged power cell, warm to the touch. Standard slot fit.",
            "state": "hidden",
            "interactable": True,
            "takeable": True,
        },
        {
            "id": "power_panel",
            "location": "command_deck",
            "description": (
                "A maintenance panel with an empty slot sized for a standard power cell. "
                "Slotting a charged cell would bring the airlock online."
            ),
            "state": "visible",
            "interactable": True,
            "takeable": False,
            "requires_tool": "power_cell",
            "provides_power": "airlock_power_ON",
        },
        {
            "id": "airlock_door",
            "location": "command_deck",
            "description": (
                "The heavy airlock door. A status light beside it is dark, waiting "
                "for power."
            ),
            "state": "locked",
            "interactable": True,
            "takeable": False,
            "requires_power": "airlock_power_ON",
            "connects_to": "airlock",
        },
    ],
    "rules": [
        "Players may only reference information already discovered in the world.",
        "Codes and facts must come from the game state — never invent them.",
        "If a fact has not been found yet, say 'unknown' rather than guessing.",
        "An action is only valid if its target is currently visible and in reach.",
    ],
    "win_condition": {"object_id": "airlock_door", "state": "unlocked"},
    "solution_path": [
        "1. Inspect captains_log -> code 0451.",
        "2. enter_code supply_locker 0451 -> take access_card.",
        "3. use access_card on command_door -> move to command_deck.",
        "4. enter_code nav_console 'reroute auxiliary' -> reveal power_cell.",
        "5. take power_cell; use power_cell on power_panel.",
        "6. airlock_door auto-unlocks -> escape.",
    ],
    "players": [
        {
            "id": "player_1",
            "name": "Dr. Aria Vega",
            "role": "Station Medic",
            "skills": ["analyze", "medical", "read_logs"],
            "backstory": "The station's physician. Methodical; trusts records.",
        },
        {
            "id": "player_2",
            "name": "Kade Rourke",
            "role": "Systems Engineer",
            "skills": ["repair", "override", "rewire"],
            "backstory": "Kept the station running for six years; knows every conduit.",
        },
    ],
    "player_clues": [
        {
            "player_id": "player_1",
            "clue": (
                "The supply locker uses the captain's old launch code. Read the "
                "captain's log inside the cryo-pod to recover it."
            ),
        },
        {
            "player_id": "player_2",
            "clue": (
                "The airlock is dead without power. At the nav console, enter the "
                "override phrase 'reroute auxiliary' to expose a spare power cell, "
                "then use the power cell on the power panel to bring the airlock online."
            ),
        },
    ],
}
