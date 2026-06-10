"""Default authored story payload for the web server.

This module loads the project's current default story in the fixed envelope
format (`{"world": ...}`), so switching defaults is a content change only.
"""

from __future__ import annotations

import json
from pathlib import Path


DEFAULT_STORY_PATH: Path = Path(__file__).with_name("world_034.json")

with DEFAULT_STORY_PATH.open(encoding="utf-8") as fh:
    DEFAULT_STORY_PAYLOAD: dict = json.load(fh)
