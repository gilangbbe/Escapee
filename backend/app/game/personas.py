"""
Modular player-persona catalog + roster builder.

This is the single place to edit, add, or re-mix the cooperative cast. Worlds in
the fixed-story format do not author players, so the engine injects a roster from
here. Each entry is a plain `PersonaSpec`; `build_roster()` turns N of them into
runtime `PlayerPersona`s with stable `player_1..player_n` ids.

Per-persona LLM binding
-----------------------
A spec (or a `build_roster` override) may set `model`/`temperature`, letting a
single team mix different local models — e.g. player_1 on ``qwen2.5:7b`` and
player_2 on ``llama3.1:8b``. When a spec leaves them as ``None`` the runner's
global defaults apply, so nothing breaks for single-model setups.

Adding / altering players
-------------------------
- Edit a `PersonaSpec` below to change a character's flavor or model.
- Append a new `PersonaSpec` to `CATALOG` (and to `DEFAULT_ORDER`) to grow the
  cast; `build_roster(count=3)` will then deal three distinct characters.
- Call `build_roster(count=n, models={"player_2": "llama3.1:8b"})` at wiring time
  to bind specific players to specific models without touching this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from app.schemas.game_setting import PlayerPersona


@dataclass(frozen=True)
class PersonaSpec:
    """An editable character template (id-free; ids are assigned by the roster)."""

    name: str
    role: str
    skills: Sequence[str]
    backstory: str = ""
    personality: str = ""
    model: Optional[str] = None
    temperature: Optional[float] = None

    def to_persona(
        self,
        player_id: str,
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> PlayerPersona:
        """Materialize a runtime persona with the given id and optional overrides."""
        return PlayerPersona(
            id=player_id,
            name=self.name,
            role=self.role,
            skills=list(self.skills),
            backstory=self.backstory,
            personality=self.personality,
            model=model if model is not None else self.model,
            temperature=temperature if temperature is not None else self.temperature,
        )


# --------------------------------------------------------------------------- #
# The catalog — edit / extend here.
# --------------------------------------------------------------------------- #
CATALOG: dict[str, PersonaSpec] = {
    "analyst": PersonaSpec(
        name="Alex Quinn",
        role="Field Analyst",
        skills=["observe", "reason", "decode"],
        backstory="A methodical investigator who tracks clues under pressure.",
        personality="Careful and deductive; reads every clue before committing.",
    ),
    "operator": PersonaSpec(
        name="Riley Sato",
        role="Systems Operator",
        skills=["repair", "override", "route_power"],
        backstory="A pragmatic engineer who can restore failing systems quickly.",
        personality="Hands-on and decisive; favors fixing and powering things.",
    ),
    "scout": PersonaSpec(
        name="Mara Vance",
        role="Scout",
        skills=["search", "navigate", "spot_hidden"],
        backstory="A restless explorer who maps unknown spaces fast.",
        personality="Bold and mobile; pushes into new rooms to widen options.",
    ),
    "specialist": PersonaSpec(
        name="Theo Nakamura",
        role="Security Specialist",
        skills=["bypass", "lockpick", "analyze_locks"],
        backstory="A former locksmith who treats every lock as a puzzle.",
        personality="Patient and precise; focuses on codes, keys, and locks.",
    ),
}

# Default dealing order when a world does not name its cast.
DEFAULT_ORDER: tuple[str, ...] = ("analyst", "operator", "scout", "specialist")


def catalog_as_dicts() -> list[dict]:
    """Serialize the catalog templates for the UI persona picker.

    Each entry carries the catalog ``key`` (so the UI can label/group templates)
    plus the editable persona fields. Ids are intentionally omitted — they are
    assigned per-roster by `build_roster`.
    """
    return [
        {
            "key": key,
            "name": spec.name,
            "role": spec.role,
            "skills": list(spec.skills),
            "backstory": spec.backstory,
            "personality": spec.personality,
            "model": spec.model,
            "temperature": spec.temperature,
        }
        for key, spec in CATALOG.items()
    ]


def build_roster(
    count: int = 2,
    *,
    order: Optional[Sequence[str]] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    models: Optional[Mapping[str, str]] = None,
    temperatures: Optional[Mapping[str, float]] = None,
) -> list[PlayerPersona]:
    """Build `count` runtime personas with stable `player_1..player_n` ids.

    Args:
        count: number of players to deal (must be >= 1).
        order: catalog keys to draw from, in order. Defaults to `DEFAULT_ORDER`.
            If `count` exceeds the order length, characters are cycled and a
            numeric suffix keeps names distinct.
        model / temperature: team-wide LLM defaults applied to every persona that
            does not get a more specific override.
        models / temperatures: per-player overrides keyed by player id
            (e.g. ``{"player_2": "llama3.1:8b"}``), highest precedence.

    Precedence (highest first): per-player override -> team-wide arg -> the spec's
    own value -> None (runner default).
    """
    if count < 1:
        raise ValueError("A roster needs at least one player.")

    keys = list(order) if order else list(DEFAULT_ORDER)
    if not keys:
        raise ValueError("Persona order is empty; cannot build a roster.")

    models = models or {}
    temperatures = temperatures or {}

    roster: list[PlayerPersona] = []
    for index in range(count):
        player_id = f"player_{index + 1}"
        key = keys[index % len(keys)]
        spec = CATALOG[key]

        persona = spec.to_persona(
            player_id,
            model=models.get(player_id, model),
            temperature=temperatures.get(player_id, temperature),
        )

        # Keep names unique when the catalog is cycled for a large cast.
        if index >= len(keys):
            persona = persona.model_copy(
                update={"name": f"{spec.name} #{index // len(keys) + 1}"}
            )
        roster.append(persona)

    return roster
