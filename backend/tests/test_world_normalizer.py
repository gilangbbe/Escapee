"""Tests for app.engine.world_normalizer — deterministic world repair layer."""

from __future__ import annotations

import json
import os

import pytest

from app.engine.world_normalizer import normalize_world, NormalizeResult


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _obj(
    id: str,
    location: str = "room_1",
    state: str = "visible",
    interactable: bool = True,
    takeable: bool = False,
    **kwargs,
) -> dict:
    o = {
        "id": id,
        "location": location,
        "description": f"desc of {id}",
        "state": state,
        "interactable": interactable,
        "takeable": takeable,
    }
    o.update(kwargs)
    return o


ROOMS = ["room_1", "room_2", "room_3"]
WIN = {"object_id": "final_lock", "state": "unlocked"}


# ------------------------------------------------------------------ #
# R1 — Functional-scenic repair
# ------------------------------------------------------------------ #

class TestR1FunctionalScenic:
    def test_scenic_with_contains_info_becomes_interactable(self):
        objs = [
            _obj("clue_paper", interactable=False, contains_info="secret_code"),
            _obj("final_lock", state="locked", requires_code="secret_code"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        clue = next(o for o in result.objects if o["id"] == "clue_paper")
        assert clue["interactable"] is True

    def test_scenic_with_fuses_becomes_interactable(self):
        objs = [
            _obj("power_switch", interactable=False, fuses={"A": "OFF"}),
            _obj("final_lock", state="locked"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        switch = next(o for o in result.objects if o["id"] == "power_switch")
        assert switch["interactable"] is True

    def test_pure_scenic_no_functional_fields_unchanged(self):
        objs = [
            _obj("decoration", interactable=False),
            _obj("final_lock", state="locked"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        deco = next(o for o in result.objects if o["id"] == "decoration")
        assert deco["interactable"] is False

    def test_already_interactable_not_touched(self):
        objs = [
            _obj("clue_paper", interactable=True, contains_info="secret_code"),
            _obj("final_lock", state="locked", requires_code="secret_code"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        repairs = [r for r in result.repairs if r.startswith("R1")]
        assert repairs == []

    def test_scenic_with_requires_tool_becomes_interactable(self):
        objs = [
            _obj("hidden_panel", interactable=False, requires_tool="screwdriver"),
            _obj("screwdriver", takeable=True),
            _obj("final_lock", state="locked"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        panel = next(o for o in result.objects if o["id"] == "hidden_panel")
        assert panel["interactable"] is True

    def test_r1_repair_logged(self):
        objs = [
            _obj("scenic_clue", interactable=False, contains_info="code_x"),
            _obj("final_lock", state="locked", requires_code="code_x"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        assert any("R1" in r and "scenic_clue" in r for r in result.repairs)


# ------------------------------------------------------------------ #
# R2 — Tool not takeable repair
# ------------------------------------------------------------------ #

class TestR2ToolNotTakeable:
    def test_non_takeable_tool_becomes_takeable(self):
        objs = [
            _obj("wrench", takeable=False),
            _obj("final_lock", state="locked", requires_tool="wrench"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        wrench = next(o for o in result.objects if o["id"] == "wrench")
        assert wrench["takeable"] is True

    def test_already_takeable_tool_unchanged(self):
        objs = [
            _obj("wrench", takeable=True),
            _obj("final_lock", state="locked", requires_tool="wrench"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        repairs = [r for r in result.repairs if r.startswith("R2")]
        assert repairs == []

    def test_r2_repair_logged(self):
        objs = [
            _obj("key_card", takeable=False),
            _obj("final_lock", state="locked", requires_tool="key_card"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        assert any("R2" in r and "key_card" in r for r in result.repairs)


# ------------------------------------------------------------------ #
# R3 — requires_code = object_id repair
# ------------------------------------------------------------------ #

class TestR3RequiresCodeIsObjectId:
    def test_requires_code_object_id_replaced_with_info_token(self):
        objs = [
            _obj("data_chip", contains_info="alpha_token_123"),
            _obj("final_lock", state="locked", requires_code="data_chip"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        lock = next(o for o in result.objects if o["id"] == "final_lock")
        assert lock["requires_code"] == "alpha_token_123"

    def test_requires_code_real_token_not_touched(self):
        objs = [
            _obj("data_chip", contains_info="alpha_token_123"),
            _obj("final_lock", state="locked", requires_code="alpha_token_123"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        lock = next(o for o in result.objects if o["id"] == "final_lock")
        assert lock["requires_code"] == "alpha_token_123"
        repairs = [r for r in result.repairs if r.startswith("R3")]
        assert repairs == []

    def test_requires_code_object_id_no_info_logged_unresolved(self):
        objs = [
            _obj("broken_panel"),  # no contains_info
            _obj("final_lock", state="locked", requires_code="broken_panel"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        assert any("UNRESOLVED" in r for r in result.repairs)

    def test_r3_repair_logged(self):
        objs = [
            _obj("note", contains_info="secret_999"),
            _obj("final_lock", state="locked", requires_code="note"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        assert any("R3" in r and "note" in r and "secret_999" in r for r in result.repairs)


# ------------------------------------------------------------------ #
# R4 — Hidden with no reveal repair
# ------------------------------------------------------------------ #

class TestR4HiddenNoReveal:
    def test_hidden_object_with_no_reveal_becomes_visible(self):
        objs = [
            _obj("lost_key", state="hidden", takeable=True),
            _obj("final_lock", state="locked", requires_tool="lost_key"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        key = next(o for o in result.objects if o["id"] == "lost_key")
        assert key["state"] == "visible"

    def test_hidden_object_with_reveal_stays_hidden(self):
        objs = [
            _obj("loose_brick", contains_info="code_x", reveals="lost_key"),
            _obj("lost_key", state="hidden", takeable=True),
            _obj("final_lock", state="locked", requires_tool="lost_key"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        key = next(o for o in result.objects if o["id"] == "lost_key")
        assert key["state"] == "hidden"

    def test_r4_repair_logged(self):
        objs = [
            _obj("buried_clue", state="hidden", contains_info="xyz"),
            _obj("final_lock", state="locked", requires_code="xyz"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        assert any("R4" in r and "buried_clue" in r for r in result.repairs)


# ------------------------------------------------------------------ #
# R5 — Derived solution path
# ------------------------------------------------------------------ #

class TestR5DerivedSolutionPath:
    def test_single_tool_path(self):
        objs = [
            _obj("hammer", takeable=True),
            _obj("final_lock", state="locked", requires_tool="hammer"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        path = result.solution_path
        assert any("hammer" in s for s in path)
        assert any("final_lock" in s for s in path)
        # Take must precede Use
        take_idx = next(i for i, s in enumerate(path) if "Take" in s and "hammer" in s)
        use_idx = next(i for i, s in enumerate(path) if "Use" in s and "hammer" in s)
        assert take_idx < use_idx

    def test_single_code_path(self):
        objs = [
            _obj("note", contains_info="code_42"),
            _obj("final_lock", state="locked", requires_code="code_42"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        path = result.solution_path
        inspect_idx = next(i for i, s in enumerate(path) if "note" in s)
        enter_idx = next(i for i, s in enumerate(path) if "Enter" in s)
        assert inspect_idx < enter_idx

    def test_chained_path_tool_then_code(self):
        """Tool unlocks an object → that object reveals a code → code opens final lock."""
        objs = [
            _obj("screwdriver", takeable=True),
            _obj("panel", state="locked", requires_tool="screwdriver", contains_info="panel_code"),
            _obj("final_lock", state="locked", requires_code="panel_code"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        path = result.solution_path
        # screwdriver must be taken before used
        take_idx = next(i for i, s in enumerate(path) if "Take" in s and "screwdriver" in s)
        use_idx = next(i for i, s in enumerate(path) if "Use" in s and "screwdriver" in s)
        enter_idx = next(i for i, s in enumerate(path) if "Enter" in s and "final_lock" in s)
        assert take_idx < use_idx < enter_idx

    def test_chained_path_code_then_tool(self):
        """Code unlocks a container → take the tool inside → tool opens final lock."""
        objs = [
            _obj("display", contains_info="unlock_code"),
            _obj("safe", state="locked", requires_code="unlock_code", takeable=True),
            _obj("final_lock", state="locked", requires_tool="safe"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        path = result.solution_path
        inspect_idx = next(i for i, s in enumerate(path) if "display" in s)
        enter_idx = next(i for i, s in enumerate(path) if "Enter" in s and "safe" in s)
        take_idx = next(i for i, s in enumerate(path) if "Take" in s and "safe" in s)
        use_idx = next(i for i, s in enumerate(path) if "Use" in s and "final_lock" in s)
        assert inspect_idx < enter_idx < take_idx < use_idx

    def test_path_replaced_by_r5_even_when_original_given(self):
        objs = [
            _obj("key", takeable=True),
            _obj("final_lock", state="locked", requires_tool="key"),
        ]
        original = ["Step 1: do something wrong", "Step 2: also wrong"]
        result = normalize_world(objs, WIN, ROOMS, original_solution_path=original)
        # Derived path should not contain the original wrong steps.
        combined = " ".join(result.solution_path)
        assert "do something wrong" not in combined
        assert "also wrong" not in combined
        # R5 logged
        assert any("R5" in r for r in result.repairs)

    def test_no_requirement_path(self):
        """Win object with no requirements → single fallback step."""
        objs = [_obj("final_lock", state="locked")]
        result = normalize_world(objs, WIN, ROOMS)
        assert len(result.solution_path) >= 1

    def test_unknown_code_source_noted_in_path(self):
        objs = [
            _obj("final_lock", state="locked", requires_code="orphan_token"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        path_text = " ".join(result.solution_path)
        assert "orphan_token" in path_text


# ------------------------------------------------------------------ #
# Combined / integration: both repairs AND path correction together
# ------------------------------------------------------------------ #

class TestCombinedRepairs:
    def test_r1_r3_combined(self):
        """Scenic object holds info; another object has requires_code = that object's id."""
        objs = [
            _obj("hidden_note", interactable=False, contains_info="master_code"),
            _obj("final_lock", state="locked", requires_code="hidden_note"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        note = next(o for o in result.objects if o["id"] == "hidden_note")
        lock = next(o for o in result.objects if o["id"] == "final_lock")
        # R1: note promoted to interactable
        assert note["interactable"] is True
        # R3: lock's requires_code replaced with info token
        assert lock["requires_code"] == "master_code"
        # Derived path mentions both
        path_text = " ".join(result.solution_path)
        assert "hidden_note" in path_text
        assert "master_code" in path_text

    def test_r2_r4_combined(self):
        """Hidden tool not takeable required by final lock."""
        objs = [
            _obj("buried_key", state="hidden", takeable=False),
            _obj("final_lock", state="locked", requires_tool="buried_key"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        key = next(o for o in result.objects if o["id"] == "buried_key")
        # R2: made takeable
        assert key["takeable"] is True
        # R4: promoted to visible
        assert key["state"] == "visible"

    def test_world_033_scenario(self):
        """Simulates the exact structural issues found in world_033.json."""
        objs = [
            # room_1
            _obj("wall_mounted_comms", contains_info="pulse_code_492"),
            _obj("magnetic_cell_lock", state="visible", takeable=True,
                 requires_code="pulse_code_492", code_digits=3),
            _obj("wrench_tool", takeable=True),
            # cell_door
            _obj("sentry_protocol_doc", location="cell_door", contains_info="sentry_sequence_884"),
            _obj("biometric_scanner", location="cell_door", state="locked",
                 requires_code="sentry_sequence_884"),
            _obj("drone_controller_base", location="cell_door", state="locked",
                 requires_tool="magnetic_cell_lock"),
            # R3 scenario: requires_code points to an object_id
            _obj("drone_hub_panel_v2", location="corridor_main_south", state="visible",
                 requires_code="sentry_protocol_doc"),
        ]
        rooms = ["room_1", "cell_door", "corridor_main", "corridor_main_south"]
        win = {"object_id": "drone_controller_base", "state": "unlocked"}

        result = normalize_world(objs, win, rooms)

        # R3: drone_hub_panel_v2.requires_code should be replaced
        hub = next(o for o in result.objects if o["id"] == "drone_hub_panel_v2")
        assert hub["requires_code"] == "sentry_sequence_884"

        # Derived path for win_condition (drone_controller_base via magnetic_cell_lock)
        path = result.solution_path
        path_text = " ".join(path)
        assert "wall_mounted_comms" in path_text
        assert "magnetic_cell_lock" in path_text
        assert "drone_controller_base" in path_text

        # Order: inspect comms → enter code → take lock → use lock on controller
        inspect_idx = next(i for i, s in enumerate(path) if "wall_mounted_comms" in s)
        enter_idx = next(i for i, s in enumerate(path) if "Enter" in s and "magnetic_cell_lock" in s)
        take_idx = next(i for i, s in enumerate(path) if "Take" in s and "magnetic_cell_lock" in s)
        use_idx = next(i for i, s in enumerate(path) if "Use" in s and "drone_controller_base" in s)
        assert inspect_idx < enter_idx < take_idx < use_idx

    def test_original_objects_not_mutated(self):
        """normalize_world must not mutate the caller's input list."""
        objs = [
            {"id": "scenic", "location": "room_1", "description": "d",
             "state": "visible", "interactable": False, "takeable": False,
             "contains_info": "code_x"},
            {"id": "final_lock", "location": "room_1", "description": "d",
             "state": "locked", "interactable": True, "takeable": False,
             "requires_code": "scenic"},
        ]
        originals = [dict(o) for o in objs]
        normalize_world(objs, WIN, ROOMS)
        for orig, after in zip(originals, objs):
            assert orig == after, f"Object '{orig['id']}' was mutated by normalize_world"


# ------------------------------------------------------------------ #
# R6 — Functional-first object ordering
# ------------------------------------------------------------------ #

class TestR6FunctionalFirst:
    def test_info_producer_sorted_before_scenic_filler(self):
        """contains_info object buried after scenic fillers is moved to front."""
        objs = [
            _obj("scenic_filler_1", interactable=False),
            _obj("scenic_filler_2", interactable=False),
            _obj("scenic_filler_3", interactable=False),
            _obj("final_lock", state="locked", requires_code="passage_code"),
            _obj("info_source", contains_info="passage_code"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        ids = [o["id"] for o in result.objects]
        assert ids.index("info_source") < ids.index("scenic_filler_1")

    def test_functional_object_sorted_before_pure_decoration(self):
        """An object with requires_code (tier 1) comes before decoration (tier 3)."""
        objs = [
            _obj("deco_a", interactable=False),
            _obj("deco_b", interactable=False),
            _obj("final_lock", state="locked", requires_code="x"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        ids = [o["id"] for o in result.objects]
        assert ids.index("final_lock") < ids.index("deco_a")

    def test_info_producer_before_consumer(self):
        """contains_info (tier 0) sorts before requires_code (tier 1)."""
        objs = [
            _obj("final_lock", state="locked", requires_code="code_x"),
            _obj("clue_card", contains_info="code_x"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        ids = [o["id"] for o in result.objects]
        assert ids.index("clue_card") < ids.index("final_lock")

    def test_stable_within_tier(self):
        """Objects within the same tier keep their relative original order."""
        objs = [
            _obj("info_a", contains_info="c1"),
            _obj("info_b", contains_info="c2"),
            _obj("deco_x", interactable=False),
            _obj("deco_y", interactable=False),
            _obj("final_lock", state="locked", requires_code="c1"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        ids = [o["id"] for o in result.objects]
        # info_a before info_b (same tier, original order preserved)
        assert ids.index("info_a") < ids.index("info_b")
        # deco_x before deco_y (same tier, original order preserved)
        assert ids.index("deco_x") < ids.index("deco_y")

    def test_r6_repair_logged_when_order_changes(self):
        objs = [
            _obj("deco", interactable=False),
            _obj("info_source", contains_info="code_z"),
            _obj("final_lock", state="locked", requires_code="code_z"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        assert any("R6" in r for r in result.repairs)

    def test_r6_no_repair_logged_when_already_ordered(self):
        """No R6 repair when functional objects are already before decoration."""
        objs = [
            _obj("info_source", contains_info="code_z"),
            _obj("final_lock", state="locked", requires_code="code_z"),
            _obj("deco", interactable=False),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        assert not any("R6" in r for r in result.repairs)

    def test_world_033_scenic_rotting_fabric_in_beam_position(self):
        """Simulates the world_033 beam-miss: scenic_rotting_fabric must be sorted
        before scenic fillers so it lands within the 5-candidate planner beam."""
        objs = [
            # closet objects in their original world_033 order
            _obj("closet_locket",    location="closet", state="locked",
                 requires_code="passage_key_code_2"),
            _obj("closet_light_bulb", location="closet", contains_info="light_bulb_clue_4"),
            _obj("closet_letter",    location="closet", state="hidden", takeable=True),
            _obj("scenic_rotting_fabric", location="closet", contains_info="passage_key_code_2",
                 scenic=True),
            _obj("scenic_old_perfume",   location="closet", interactable=False, scenic=True),
            _obj("scenic_distorted",     location="closet", interactable=False, scenic=True),
            _obj("scenic_filler_1",      location="closet", interactable=False, scenic=True),
            _obj("scenic_filler_2",      location="closet", interactable=False, scenic=True),
        ]
        win = {"object_id": "closet_locket", "state": "unlocked"}
        result = normalize_world(objs, win, ["closet"])
        ids = [o["id"] for o in result.objects]
        # scenic_rotting_fabric (tier 0: contains_info) must come before the
        # locked consumer (tier 1: requires_code) and all pure scenic fillers.
        rf_pos = ids.index("scenic_rotting_fabric")
        locket_pos = ids.index("closet_locket")
        filler_pos = ids.index("scenic_filler_1")
        assert rf_pos < locket_pos, "info producer must precede code consumer"
        assert rf_pos < filler_pos, "info producer must precede scenic filler"


# ------------------------------------------------------------------ #
# R7 — Numeric requires_code relinked to info token
# ------------------------------------------------------------------ #

class TestR7NumericCodeRelink:
    def test_bare_digit_code_relinked_to_matching_info_token(self):
        """requires_code="321" with no direct chain → relinked to "symbol_code_321"."""
        objs = [
            _obj("final_symbol", contains_info="symbol_code_321"),
            _obj("final_lock", state="locked", requires_code="321"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        lock = next(o for o in result.objects if o["id"] == "final_lock")
        assert lock["requires_code"] == "symbol_code_321"

    def test_r7_repair_logged(self):
        objs = [
            _obj("note", contains_info="music_code_972"),
            _obj("final_lock", state="locked", requires_code="972"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        assert any("R7" in r and "final_lock" in r and "music_code_972" in r for r in result.repairs)

    def test_ambiguous_multiple_matching_tokens_left_unchanged(self):
        """Two info tokens share the same suffix → ambiguous, code left as-is."""
        objs = [
            _obj("clue_a", contains_info="door_code_321"),
            _obj("clue_b", contains_info="box_code_321"),
            _obj("final_lock", state="locked", requires_code="321"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        lock = next(o for o in result.objects if o["id"] == "final_lock")
        assert lock["requires_code"] == "321"
        assert any("R7" in r and "ambiguous" in r for r in result.repairs)

    def test_no_matching_suffix_left_unchanged(self):
        """No info token ends with the digit suffix → stand-alone code, no action."""
        objs = [
            _obj("clue", contains_info="other_code_999"),
            _obj("final_lock", state="locked", requires_code="321"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        lock = next(o for o in result.objects if o["id"] == "final_lock")
        assert lock["requires_code"] == "321"
        assert not any("R7" in r for r in result.repairs)

    def test_direct_chain_intact_not_touched(self):
        """If some object has contains_info == requires_code already, leave as-is."""
        objs = [
            _obj("clue", contains_info="321"),  # verbatim digit token
            _obj("final_lock", state="locked", requires_code="321"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        lock = next(o for o in result.objects if o["id"] == "final_lock")
        assert lock["requires_code"] == "321"
        assert not any("R7" in r for r in result.repairs)

    def test_token_style_requires_code_not_affected(self):
        """Token-style requires_code (not all digits) is never touched by R7."""
        objs = [
            _obj("clue", contains_info="compass_code_9"),
            _obj("final_lock", state="locked", requires_code="compass_code_9"),
        ]
        result = normalize_world(objs, WIN, ROOMS)
        lock = next(o for o in result.objects if o["id"] == "final_lock")
        assert lock["requires_code"] == "compass_code_9"
        assert not any("R7" in r for r in result.repairs)

    def test_world_033_all_code_locks_relinked(self):
        """Simulates the exact world_033 pattern: all pure-digit codes relinked to tokens."""
        objs = [
            _obj("torn_map",                contains_info="map_code_128"),
            _obj("bloodstained_music_sheet",contains_info="music_code_972"),
            _obj("yellowed_letters",        contains_info="letter_code_543"),
            _obj("cracked_mirror",          contains_info="mirror_code_789"),
            _obj("final_symbol",            contains_info="symbol_code_321"),
            _obj("parlor_door",  state="locked", requires_tool="rusty_key"),
            _obj("study_door",   state="locked", requires_code="972"),
            _obj("attic_door",   state="locked", requires_code="543"),
            _obj("hidden_lock",  state="locked", requires_code="789"),
            _obj("crypt_exit",   state="locked", requires_code="321"),
            _obj("rusty_key",    takeable=True),
        ]
        win = {"object_id": "crypt_exit", "state": "unlocked"}
        result = normalize_world(objs, win, ROOMS)

        by_id = {o["id"]: o for o in result.objects}
        assert by_id["study_door"]["requires_code"] == "music_code_972"
        assert by_id["attic_door"]["requires_code"] == "letter_code_543"
        assert by_id["hidden_lock"]["requires_code"] == "mirror_code_789"
        assert by_id["crypt_exit"]["requires_code"] == "symbol_code_321"
        # map_code_128 has no lock pointing to it — should be unaffected
        r7_repairs = [r for r in result.repairs if r.startswith("R7")]
        assert len(r7_repairs) == 4

    def test_world_033_solution_path_traces_info_chain(self):
        """After R7 relinking, _derive_solution_path correctly traces crypt_exit → final_symbol."""
        objs = [
            _obj("final_symbol", contains_info="symbol_code_321"),
            _obj("crypt_exit", state="locked", requires_code="321"),
        ]
        win = {"object_id": "crypt_exit", "state": "unlocked"}
        result = normalize_world(objs, win, ROOMS)
        path_text = " ".join(result.solution_path)
        # After R7: requires_code is "symbol_code_321" so path traces to final_symbol
        assert "final_symbol" in path_text, "solution path must reference the info source"
        assert "symbol_code_321" in path_text
        assert "source unknown" not in path_text


# ------------------------------------------------------------------ #
# Integration: full world JSON files load and normalize cleanly
# ------------------------------------------------------------------ #

GAME_DIR = os.path.join(os.path.dirname(__file__), "..", "app", "game")


@pytest.mark.parametrize("world_file", [
    "world_015.json", "world_016.json", "world_017.json",
    "world_018.json", "world_019.json", "world_020.json",
    "world_021.json", "world_033.json",
])
def test_world_loads_and_normalizes_without_crash(world_file):
    """Each world should load through load_setting_compat without exception."""
    from app.schemas.fixed_world import load_setting_compat
    path = os.path.join(GAME_DIR, world_file)
    if not os.path.exists(path):
        pytest.skip(f"{world_file} not found")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    gs = load_setting_compat(data)
    assert gs.win_condition.object_id in {o.id for o in gs.objects}
    assert len(gs.solution_path) >= 1
