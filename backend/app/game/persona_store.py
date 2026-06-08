"""
Persistent, editable persona catalog.

The cast used to live only in `app.game.personas.CATALOG` (hardcoded Python). To
let external clients — the web UI, an iOS app, etc. — add / edit / remove
personas WITHOUT touching code, this module mirrors that catalog into a JSON
file and exposes plain CRUD helpers plus a REST-friendly dict shape.

On first use the store seeds itself from `catalog_as_dicts()`, so behavior is
identical to the hardcoded catalog until someone edits it. Each record is keyed
by a stable, URL-safe ``key`` and carries the editable persona fields (including
``gender``). All writes are guarded by a process-level lock and the JSON file is
the single source of truth for the HTTP persona endpoints.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Optional

from app.game.personas import catalog_as_dicts

STORE_PATH = Path(__file__).with_name("personas_store.json")

_LOCK = threading.RLock()


def _seed() -> list[dict]:
    return catalog_as_dicts()


def _load_unlocked() -> list[dict]:
    """Read the store, seeding + persisting defaults on first run or corruption."""
    if not STORE_PATH.exists():
        data = _seed()
        _save_unlocked(data)
        return data
    try:
        raw = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return _seed()
    if not isinstance(raw, list):
        return _seed()
    return [r for r in raw if isinstance(r, dict) and r.get("key")]


def _save_unlocked(items: list[dict]) -> None:
    STORE_PATH.write_text(json.dumps(items, indent=2), encoding="utf-8")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")
    return slug or "persona"


def _normalize(data: dict) -> dict:
    """Coerce an arbitrary payload into a clean persona record (without key)."""
    skills = data.get("skills") or []
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",")]
    temperature = data.get("temperature")
    model = data.get("model")
    return {
        "name": str(data.get("name", "")).strip() or "Unnamed",
        "role": str(data.get("role", "")).strip() or "Crew",
        "gender": str(data.get("gender", "")).strip(),
        "skills": [str(s).strip() for s in skills if str(s).strip()],
        "backstory": str(data.get("backstory", "")),
        "personality": str(data.get("personality", "")),
        "model": str(model).strip() or None if model else None,
        "temperature": (
            float(temperature) if temperature not in (None, "") else None
        ),
    }


def list_personas() -> list[dict]:
    """All stored persona records (seeded from the catalog on first call)."""
    with _LOCK:
        return _load_unlocked()


def get_persona(key: str) -> Optional[dict]:
    with _LOCK:
        for item in _load_unlocked():
            if item.get("key") == key:
                return item
    return None


def create_persona(data: dict) -> dict:
    """Append a new persona, allocating a unique key from its name (or supplied)."""
    with _LOCK:
        items = _load_unlocked()
        existing = {i.get("key") for i in items}
        base = _slugify(data.get("key") or data.get("name", ""))
        key = base
        suffix = 2
        while key in existing:
            key = f"{base}_{suffix}"
            suffix += 1
        record = {"key": key, **_normalize(data)}
        items.append(record)
        _save_unlocked(items)
        return record


def update_persona(key: str, data: dict) -> Optional[dict]:
    """Patch an existing persona in place. Returns None if the key is unknown."""
    with _LOCK:
        items = _load_unlocked()
        for index, item in enumerate(items):
            if item.get("key") == key:
                merged = {**item, **data}
                items[index] = {"key": key, **_normalize(merged)}
                _save_unlocked(items)
                return items[index]
    return None


def delete_persona(key: str) -> bool:
    """Remove a persona by key. Returns True if a record was removed."""
    with _LOCK:
        items = _load_unlocked()
        remaining = [i for i in items if i.get("key") != key]
        if len(remaining) == len(items):
            return False
        _save_unlocked(remaining)
        return True
