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

    def test_world_022_scenario(self):
        """Simulates the exact structural issues found in world_022.json."""
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
# Integration: full world JSON files load and normalize cleanly
# ------------------------------------------------------------------ #

GAME_DIR = os.path.join(os.path.dirname(__file__), "..", "app", "game")


@pytest.mark.parametrize("world_file", [
    "world_015.json", "world_016.json", "world_017.json",
    "world_022.json", "world_022.json", "world_022.json", "world_021.json",
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
