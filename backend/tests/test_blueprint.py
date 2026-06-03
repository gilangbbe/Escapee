"""Tests for the GM blueprint schema and the JSON validate/repair loop."""

from __future__ import annotations

import copy
import json

import pytest

from app.llm.json_repair import extract_json_object, parse_and_validate
from app.schemas.blueprint import RoomBlueprint
from app.schemas.sample_blueprint import SAMPLE_BLUEPRINT


# --------------------------------------------------------------------------- #
# Schema validation
# --------------------------------------------------------------------------- #
def test_sample_blueprint_is_valid():
    bp = RoomBlueprint.model_validate(SAMPLE_BLUEPRINT)
    assert bp.title == "The Forgotten Lighthouse"
    assert bp.start_room_id == "keepers_quarters"
    assert len(bp.players) == 2


def test_bad_start_room_rejected():
    bad = copy.deepcopy(SAMPLE_BLUEPRINT)
    bad["start_room_id"] = "nowhere"
    with pytest.raises(ValueError, match="start_room_id"):
        RoomBlueprint.model_validate(bad)


def test_exit_to_unknown_room_rejected():
    bad = copy.deepcopy(SAMPLE_BLUEPRINT)
    bad["rooms"][0]["exits"][0]["to_room_id"] = "ghost_room"
    with pytest.raises(ValueError, match="unknown room"):
        RoomBlueprint.model_validate(bad)


def test_key_lock_pointing_to_nonitem_rejected():
    bad = copy.deepcopy(SAMPLE_BLUEPRINT)
    # door_lock is a key lock; point it at a non-existent item.
    for lock in bad["locks"]:
        if lock["id"] == "door_lock":
            lock["key_item_id"] = "imaginary_key"
    with pytest.raises(ValueError, match="not an item"):
        RoomBlueprint.model_validate(bad)


def test_code_lock_missing_solution_rejected():
    bad = copy.deepcopy(SAMPLE_BLUEPRINT)
    for lock in bad["locks"]:
        if lock["id"] == "safe_lock":
            lock["code_solution"] = None
    with pytest.raises(ValueError, match="code lock requires"):
        RoomBlueprint.model_validate(bad)


def test_puzzle_unlock_target_must_be_lock():
    bad = copy.deepcopy(SAMPLE_BLUEPRINT)
    for pz in bad["puzzles"]:
        if pz["id"] == "safe_code_puzzle":
            pz["reward_target_id"] = "logbook"  # an item, not a lock
    with pytest.raises(ValueError, match="not a lock"):
        RoomBlueprint.model_validate(bad)


# --------------------------------------------------------------------------- #
# JSON extraction + repair loop (no LLM needed)
# --------------------------------------------------------------------------- #
def test_extract_json_from_code_fence():
    raw = "Sure! Here is your room:\n```json\n{\"a\": 1}\n```\nEnjoy!"
    assert extract_json_object(raw) == '{"a": 1}'


def test_extract_json_with_surrounding_prose():
    raw = 'Here you go: {"a": {"b": 2}} -- done.'
    assert extract_json_object(raw) == '{"a": {"b": 2}}'


def test_parse_and_validate_success_on_clean_json():
    raw = json.dumps(SAMPLE_BLUEPRINT)
    result = parse_and_validate(raw, RoomBlueprint)
    assert result.ok
    assert result.value is not None
    assert result.value.theme.startswith("abandoned")


def test_parse_and_validate_no_json_gives_repair_instruction():
    result = parse_and_validate("I cannot do that.", RoomBlueprint)
    assert not result.ok
    assert result.repair_instruction is not None
    assert "JSON" in result.repair_instruction


def test_parse_and_validate_schema_error_gives_repair_instruction():
    bad = copy.deepcopy(SAMPLE_BLUEPRINT)
    bad["start_room_id"] = "nowhere"
    result = parse_and_validate(json.dumps(bad), RoomBlueprint)
    assert not result.ok
    assert result.repair_instruction is not None
    assert "schema" in result.repair_instruction.lower()
