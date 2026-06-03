"""
"Orbital Decay" — the canonical hand-authored escape room.

Designed by hand (not by the GM LLM) to serve as a known-good, fully solvable
game for building and testing the World Simulator (Phase 2).

DESIGN NOTES (why it is solvable & cooperative):
  Solution path (requires BOTH players' private clues):
    1. Vega reads the captain's log in the cryo pod -> learns locker code 0451.
    2. unlock supply_locker (code 0451) -> take access_card.
    3. unlock command_door_lock (key = access_card) -> move to command_deck.
    4. Rourke solves 'reroute_power' -> reveals hidden power_cell on command_deck.
    5. take power_cell.
    6. unlock airlock_lock (sequence: power_cell -> power_panel) -> move to airlock.
    7. Reaching 'airlock' satisfies the win condition.

  Asymmetric information:
    - player_1 (Vega) alone knows where the locker code comes from.
    - player_2 (Rourke) alone knows how to expose & use the power cell.

  Anti-strand guarantees (enforced by the Phase 1 integrity validator):
    - The only hidden item (power_cell) is revealed by the 'reroute_power' puzzle.
    - Every lock has a reachable solution.
"""

from __future__ import annotations

ORBITAL_DECAY: dict = {
    "title": "Orbital Decay",
    "theme": "a derelict orbital research station with failing life support",
    "intro_narrative": (
        "Emergency lighting bathes the station in red. Life support is failing and "
        "the oxygen reserve reads 18 minutes. Two of you wake from cryo-sleep in a "
        "sealed bay. The only way home is the airlock on the command deck — but "
        "nothing here works without power, and everything is locked down."
    ),
    "start_room_id": "cryo_bay",
    "rooms": [
        {
            "id": "cryo_bay",
            "name": "Cryo Bay",
            "description": (
                "A frost-rimed chamber of cryo-pods. A heavy supply locker is bolted "
                "to one wall. A sealed hatch leads toward the command deck."
            ),
            "objects": ["cryo_pod", "supply_locker"],
            "items": [],
            "exits": [
                {
                    "direction": "command_hatch",
                    "to_room_id": "command_deck",
                    "lock_id": "command_door_lock",
                }
            ],
        },
        {
            "id": "command_deck",
            "name": "Command Deck",
            "description": (
                "Dead consoles and a darkened navigation station. A power panel hums "
                "faintly, waiting for a charge. The airlock door dominates the far wall."
            ),
            "objects": ["nav_console", "power_panel"],
            # power_cell starts hidden here; revealed by the reroute_power puzzle.
            "items": ["power_cell"],
            "exits": [
                {
                    "direction": "airlock_door",
                    "to_room_id": "airlock",
                    "lock_id": "airlock_lock",
                }
            ],
        },
        {
            "id": "airlock",
            "name": "Airlock",
            "description": (
                "The airlock cycles open to a docked escape pod. Fresh air floods in. "
                "You have escaped Orbital Decay."
            ),
            "objects": [],
            "items": [],
            "exits": [],
        },
    ],
    "objects": [
        {
            "id": "cryo_pod",
            "name": "Captain's Cryo-Pod",
            "description": (
                "A cracked cryo-pod, long since cold. A data-slate logbook rests inside."
            ),
            "hidden": False,
            "is_container": True,
            "contains": ["captains_log"],
            "lock_id": None,
        },
        {
            "id": "supply_locker",
            "name": "Supply Locker",
            "description": (
                "A reinforced locker secured by a four-digit keypad. Something rattles inside."
            ),
            "hidden": False,
            "is_container": True,
            "contains": ["access_card"],
            "lock_id": "locker_lock",
        },
        {
            "id": "nav_console",
            "name": "Navigation Console",
            "description": (
                "A multi-bay navigation console. An auxiliary power feed can be rerouted "
                "here by someone who knows the override."
            ),
            "hidden": False,
            "is_container": False,
            "contains": [],
            "lock_id": None,
        },
        {
            "id": "power_panel",
            "name": "Power Panel",
            "description": (
                "A maintenance panel with an empty slot sized for a standard power cell. "
                "Slotting a charged cell would bring the airlock online."
            ),
            "hidden": False,
            "is_container": False,
            "contains": [],
            "lock_id": None,
        },
    ],
    "items": [
        {
            "id": "captains_log",
            "name": "Captain's Logbook",
            "description": (
                "The final entry: 'Sealed our gear behind the old launch code — "
                "zero-four-five-one. If you're reading this, I didn't make it.'"
            ),
            "hidden": False,
        },
        {
            "id": "access_card",
            "name": "Command Access Card",
            "description": "A magnetic keycard stamped 'COMMAND DECK — AUTHORIZED ONLY'.",
            "hidden": False,
        },
        {
            "id": "power_cell",
            "name": "Spare Power Cell",
            "description": "A charged power cell, warm to the touch. Standard slot fit.",
            "hidden": True,
        },
    ],
    "locks": [
        {
            "id": "locker_lock",
            "lock_type": "code",
            "locked": True,
            "key_item_id": None,
            "code_solution": "0451",
            "sequence_solution": [],
            "hint": "A four-digit launch code the captain favored.",
        },
        {
            "id": "command_door_lock",
            "lock_type": "key",
            "locked": True,
            "key_item_id": "access_card",
            "code_solution": None,
            "sequence_solution": [],
            "hint": "Requires command-deck authorization.",
        },
        {
            "id": "airlock_lock",
            "lock_type": "sequence",
            "locked": True,
            "key_item_id": None,
            "code_solution": None,
            # Slot the power cell, then engage the power panel.
            "sequence_solution": ["power_cell", "power_panel"],
            "hint": "The airlock needs power before it will cycle.",
        },
    ],
    "puzzles": [
        {
            "id": "reroute_power",
            "description": (
                "Reroute the auxiliary power feed at the navigation console to expose "
                "the station's spare power cell."
            ),
            "solution": "reroute auxiliary",
            "reward_type": "reveal_item",
            "reward_target_id": "power_cell",
        }
    ],
    "players": [
        {
            "id": "player_1",
            "name": "Dr. Aria Vega",
            "role": "Station Medic",
            "skills": ["analyze", "medical", "read_logs"],
            "backstory": (
                "The station's physician. Methodical, reads everything twice, trusts records."
            ),
        },
        {
            "id": "player_2",
            "name": "Kade Rourke",
            "role": "Systems Engineer",
            "skills": ["repair", "override", "rewire"],
            "backstory": (
                "Kept the station running for six years. Knows every cable and conduit."
            ),
        },
    ],
    "player_clues": [
        {
            "player_id": "player_1",
            "clue": (
                "The supply locker uses the captain's old launch code. The captain's "
                "log is in the cryo-pod — read it to recover the code."
            ),
        },
        {
            "player_id": "player_2",
            "clue": (
                "The airlock is dead without power. Reroute the auxiliary feed at the "
                "navigation console (override phrase: 'reroute auxiliary') to expose a "
                "spare power cell, then slot it into the power panel to cycle the airlock."
            ),
        },
    ],
    "win_conditions": [
        {
            "win_type": "reach_room",
            "target_id": "airlock",
            "description": "Both survivors reach the airlock and escape the station.",
        }
    ],
}
