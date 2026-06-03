"""Tests for the GameSetting (v2) schema + integrity validator."""

from __future__ import annotations

import copy
import json
import os

import pytest

from app.game.orbital_decay_setting import ORBITAL_DECAY_SETTING
from app.schemas.game_setting import GameSetting

CHEM_JSON_PATH = os.path.join(
    os.path.dirname(__file__), "..", "app", "game", "game_setting.json"
)


def test_orbital_decay_setting_is_valid():
    gs = GameSetting.model_validate(ORBITAL_DECAY_SETTING)
    assert gs.rooms == ["cryo_bay", "command_deck", "airlock"]
    assert gs.win_condition.object_id == "airlock_door"
    assert len(gs.players) == 2


def test_provided_chemistry_json_validates():
    with open(os.path.abspath(CHEM_JSON_PATH), encoding="utf-8") as fh:
        data = json.load(fh)
    gs = GameSetting.model_validate(data)
    assert "lab_utama" in gs.rooms
    assert gs.win_condition.object_id == "pintu_darurat"
    # Nested location: a bottle lives on the workbench object, not a room.
    botol = gs.object("botol_merah")
    assert botol is not None and botol.location == "meja_kerja"


def test_unknown_location_rejected():
    bad = copy.deepcopy(ORBITAL_DECAY_SETTING)
    bad["objects"][0]["location"] = "nowhere"
    with pytest.raises(ValueError, match="neither room nor object"):
        GameSetting.model_validate(bad)


def test_requires_tool_must_be_object():
    bad = copy.deepcopy(ORBITAL_DECAY_SETTING)
    for o in bad["objects"]:
        if o["id"] == "command_door":
            o["requires_tool"] = "imaginary_card"
    with pytest.raises(ValueError, match="requires_tool"):
        GameSetting.model_validate(bad)


def test_win_target_must_exist():
    bad = copy.deepcopy(ORBITAL_DECAY_SETTING)
    bad["win_condition"]["object_id"] = "ghost_door"
    with pytest.raises(ValueError, match="win_condition"):
        GameSetting.model_validate(bad)


def test_containment_cycle_rejected():
    bad = copy.deepcopy(ORBITAL_DECAY_SETTING)
    # Make two objects contain each other.
    a, b = bad["objects"][0], bad["objects"][1]
    a["location"] = b["id"]
    b["location"] = a["id"]
    with pytest.raises(ValueError, match="cycle"):
        GameSetting.model_validate(bad)


def test_reveals_must_be_object():
    bad = copy.deepcopy(ORBITAL_DECAY_SETTING)
    for o in bad["objects"]:
        if o["id"] == "nav_console":
            o["reveals"] = "phantom_cell"
    with pytest.raises(ValueError, match="reveals"):
        GameSetting.model_validate(bad)
