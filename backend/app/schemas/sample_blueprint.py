"""A hand-authored valid blueprint used as a fixture and reference example."""

from __future__ import annotations

SAMPLE_BLUEPRINT: dict = {
    "title": "The Forgotten Lighthouse",
    "theme": "abandoned coastal lighthouse at night",
    "intro_narrative": (
        "The heavy door slams shut behind you. Wind howls through the old "
        "lighthouse. A single lantern flickers. You must find a way out before "
        "the tide floods the lower level."
    ),
    "start_room_id": "keepers_quarters",
    "rooms": [
        {
            "id": "keepers_quarters",
            "name": "Keeper's Quarters",
            "description": "A cramped room with a writing desk and a rusted iron door.",
            "objects": ["writing_desk", "wall_safe"],
            "items": [],
            "exits": [
                {"direction": "iron_door", "to_room_id": "lamp_room", "lock_id": "door_lock"}
            ],
        },
        {
            "id": "lamp_room",
            "name": "Lamp Room",
            "description": "The top of the lighthouse. The great lamp is dark. A hatch leads to freedom.",
            "objects": ["great_lamp"],
            "items": [],
            "exits": [
                {"direction": "escape_hatch", "to_room_id": "exterior", "lock_id": "hatch_lock"}
            ],
        },
        {
            "id": "exterior",
            "name": "Lighthouse Balcony",
            "description": "Cold sea air. You have escaped the lighthouse.",
            "objects": [],
            "items": [],
            "exits": [],
        },
    ],
    "objects": [
        {
            "id": "writing_desk",
            "name": "Writing Desk",
            "description": "An old desk. A drawer holds a faded logbook.",
            "is_container": True,
            "contains": ["logbook"],
            "lock_id": None,
            "hidden": False,
        },
        {
            "id": "wall_safe",
            "name": "Wall Safe",
            "description": "A small iron safe set into the wall, sealed with a 4-digit dial.",
            "is_container": True,
            "contains": ["brass_key"],
            "lock_id": "safe_lock",
            "hidden": False,
        },
        {
            "id": "great_lamp",
            "name": "Great Lamp",
            "description": "The lighthouse's huge lamp mechanism. A panel needs power.",
            "is_container": False,
            "contains": [],
            "lock_id": None,
            "hidden": False,
        },
    ],
    "items": [
        {
            "id": "logbook",
            "name": "Keeper's Logbook",
            "description": "Most pages are water-damaged. One entry reads: 'The year she vanished: 1887.'",
            "hidden": False,
        },
        {
            "id": "brass_key",
            "name": "Brass Key",
            "description": "A heavy brass key, worn smooth with age.",
            "hidden": False,
        },
        {
            "id": "fuse",
            "name": "Ceramic Fuse",
            "description": "A spare fuse that could restore power to the lamp.",
            "hidden": True,
        },
    ],
    "locks": [
        {
            "id": "safe_lock",
            "lock_type": "code",
            "locked": True,
            "key_item_id": None,
            "code_solution": "1887",
            "sequence_solution": [],
            "hint": "A four-digit year, mentioned somewhere you keep records.",
        },
        {
            "id": "door_lock",
            "lock_type": "key",
            "locked": True,
            "key_item_id": "brass_key",
            "code_solution": None,
            "sequence_solution": [],
            "hint": "Needs a heavy key.",
        },
        {
            "id": "hatch_lock",
            "lock_type": "sequence",
            "locked": True,
            "key_item_id": None,
            "code_solution": None,
            "sequence_solution": ["fuse", "great_lamp"],
            "hint": "The hatch only releases once the beacon shines again.",
        },
    ],
    "puzzles": [
        {
            "id": "safe_code_puzzle",
            "description": "Decode the 4-digit safe combination from the logbook.",
            "solution": "1887",
            "reward_type": "unlock",
            "reward_target_id": "safe_lock",
        },
        {
            "id": "hidden_fuse_puzzle",
            "description": "Search the lamp room thoroughly to find a spare part.",
            "solution": "search_lamp_room",
            "reward_type": "reveal_item",
            "reward_target_id": "fuse",
        },
    ],
    "players": [
        {
            "id": "player_1",
            "name": "Mara the Archivist",
            "role": "Researcher",
            "skills": ["decode_text", "read_logs"],
            "backstory": "A historian obsessed with maritime records.",
        },
        {
            "id": "player_2",
            "name": "Cole the Engineer",
            "role": "Engineer",
            "skills": ["repair", "rewire"],
            "backstory": "A former lighthouse mechanic.",
        },
    ],
    "player_clues": [
        {
            "player_id": "player_1",
            "clue": "Safe codes in this lighthouse are always meaningful YEARS from the logbook.",
        },
        {
            "player_id": "player_2",
            "clue": "The escape hatch is wired to the lamp: restore the lamp and the hatch releases.",
        },
    ],
    "win_conditions": [
        {
            "win_type": "reach_room",
            "target_id": "exterior",
            "description": "Both keepers escape onto the balcony.",
        }
    ],
}
