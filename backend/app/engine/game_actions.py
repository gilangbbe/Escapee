"""
Player action schema for the GameSetting (v2) engine.

Players (LLMs) emit ONE validated `GameAction` per turn. The simulator only ever
consumes validated actions — never free text — which is the core
anti-hallucination guard at the action boundary.

Fields used per verb:
  look                                  -> (none)
  inspect    target_id                  -> object to examine
  take       target_id                  -> object to pick up (must be takeable)
  enter_code target_id, code            -> enter a code on an object
  use        item_id, target_id         -> use a held item on an object
  set_fuse   target_id, fuse, position  -> flip a fuse ('ON'|'OFF') on a panel
  move       to_room                    -> walk to a connected, accessible room
  give       item_id, to_player_id      -> hand an item to a player in the room
  say        message                    -> communicate (no world effect)
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class GameActionType(str, Enum):
    LOOK = "look"
    INSPECT = "inspect"
    TAKE = "take"
    ENTER_CODE = "enter_code"
    USE = "use"
    SET_FUSE = "set_fuse"
    MOVE = "move"
    GIVE = "give"
    SAY = "say"


class GameAction(BaseModel):
    action: GameActionType

    target_id: Optional[str] = Field(None, description="Object the action targets.")
    item_id: Optional[str] = Field(None, description="Held item, for 'use'/'give'.")
    code: Optional[str] = Field(None, description="Code value, for 'enter_code'.")
    fuse: Optional[str] = Field(None, description="Fuse letter, for 'set_fuse'.")
    position: Optional[str] = Field(None, description="'ON'|'OFF', for 'set_fuse'.")
    to_room: Optional[str] = Field(None, description="Destination room, for 'move'.")
    to_player_id: Optional[str] = Field(None, description="Recipient, for 'give'.")
    message: Optional[str] = Field(None, description="Spoken text, for 'say'.")

    @model_validator(mode="after")
    def _normalize_ids(self) -> "GameAction":
        """Defensive: strip display brackets/whitespace small models copy verbatim.

        The state view shows ids wrapped as ``[captains_log]`` for readability;
        7B models often echo the brackets into the JSON. We normalize id-bearing
        fields so ``"[captains_log]"`` resolves to ``"captains_log"``.
        """
        for field in ("target_id", "item_id", "to_room", "to_player_id"):
            value = getattr(self, field)
            if isinstance(value, str):
                cleaned = value.strip().strip("[]").strip()
                if cleaned != value:
                    setattr(self, field, cleaned)

        # Common 7B slip: for `use`, the model sometimes puts the object id in
        # to_player_id (a GIVE field). Auto-correct it into target_id.
        if (
            self.action == GameActionType.USE
            and not self.target_id
            and self.to_player_id
        ):
            self.target_id = self.to_player_id
            self.to_player_id = None
        return self

    @model_validator(mode="after")
    def _validate_required_fields(self) -> "GameAction":
        """Enforce verb-specific required fields before engine execution."""
        required: dict[GameActionType, tuple[str, ...]] = {
            GameActionType.LOOK: (),
            GameActionType.INSPECT: ("target_id",),
            GameActionType.TAKE: ("target_id",),
            GameActionType.ENTER_CODE: ("target_id", "code"),
            GameActionType.USE: ("item_id", "target_id"),
            GameActionType.SET_FUSE: ("target_id", "fuse", "position"),
            GameActionType.MOVE: ("to_room",),
            GameActionType.GIVE: ("item_id", "to_player_id"),
            GameActionType.SAY: ("message",),
        }
        missing = [
            field_name
            for field_name in required[self.action]
            if not getattr(self, field_name)
        ]
        if missing:
            raise ValueError(
                f"action '{self.action.value}' requires field(s): {', '.join(missing)}"
            )

        if self.action == GameActionType.SET_FUSE and self.position not in {"ON", "OFF"}:
            raise ValueError("action 'set_fuse' requires position to be 'ON' or 'OFF'")

        return self
