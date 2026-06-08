"""
FastAPI application (Phase 5).

Endpoints:
  GET  /                 -> health / banner
  GET  /api/setting      -> the current GameSetting as setup metadata (for the UI)
  WS   /ws/game          -> start a game and stream setup/event/state/result JSON

The WebSocket accepts optional query params:
  model    : Ollama model name to drive every player agent (default "llama3")
  rounds   : max rounds before the game stops (default 30)

The default game is the fixed-format authored story payload in
`app/game/output 2.json`. The simulator
is the single source of truth; the socket only streams what the orchestrator
produces. CORS is open to the Vite dev server origins for local development.
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.game import persona_store
from app.game.default_story import DEFAULT_STORY_PAYLOAD
from app.llm.ollama_client import DEFAULT_OLLAMA_URL, list_installed_models
from app.schemas.fixed_world import load_setting_compat
from app.schemas.game_setting import GameSetting, PlayerPersona
from app.web.runner import DEFAULT_MODEL, GameRunner, build_agents, build_narrator
from app.web.serializers import setup_message

app = FastAPI(title="Multi-LLM Escape Room", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PersonaPayload(BaseModel):
    """Editable persona fields accepted by the REST persona endpoints.

    Designed to be consumed by any client (web UI, iOS app, ...). ``key`` is only
    honored on create as a slug hint; updates address the persona by URL path.
    """

    name: str
    role: str = "Crew"
    gender: str = ""
    skills: list[str] = Field(default_factory=list)
    backstory: str = ""
    personality: str = ""
    model: Optional[str] = None
    temperature: Optional[float] = None
    key: Optional[str] = None



def current_setting() -> GameSetting:
    """The game served by this instance.

    Accepts either the legacy GameSetting dict or the new fixed-world envelope
    format ({"world": ...}), then normalizes to runtime GameSetting.
    """
    return load_setting_compat(DEFAULT_STORY_PAYLOAD)


@app.get("/")
async def root() -> dict:
    return {"name": "Multi-LLM Escape Room", "status": "ok"}


@app.get("/api/setting")
async def get_setting() -> dict:
    """Scenario + personas metadata for the UI before a game starts."""
    return setup_message(current_setting())


@app.get("/api/personas")
async def get_personas() -> dict:
    """Persona-editor bootstrap data for any client.

    Returns the current default roster (so an editor opens pre-filled), the
    persistent catalog of reusable persona templates, and the list of models
    installed on the local Ollama server (best-effort; empty if unreachable).
    """
    setting = current_setting()
    installed = await list_installed_models(DEFAULT_OLLAMA_URL)
    # Always include the configured default so it is selectable even if the
    # tags lookup failed or that model is pulled lazily.
    models = sorted(set(installed) | {DEFAULT_MODEL})
    return {
        "default_model": DEFAULT_MODEL,
        "models": models,
        "catalog": persona_store.list_personas(),
        "roster": setup_message(setting)["players"],
    }


# --------------------------------------------------------------------------- #
# Persona catalog CRUD — a plain REST resource any client (iOS, web) can use.
# --------------------------------------------------------------------------- #
@app.get("/api/personas/catalog")
async def list_persona_catalog() -> list[dict]:
    """Every stored persona template (seeded from code on first run)."""
    return persona_store.list_personas()


@app.get("/api/personas/catalog/{key}")
async def get_persona_template(key: str) -> dict:
    persona = persona_store.get_persona(key)
    if persona is None:
        raise HTTPException(status_code=404, detail=f"persona '{key}' not found")
    return persona


@app.post("/api/personas/catalog", status_code=201)
async def create_persona_template(payload: PersonaPayload) -> dict:
    """Add a new persona to the catalog and persist it."""
    return persona_store.create_persona(payload.model_dump())


@app.put("/api/personas/catalog/{key}")
async def update_persona_template(key: str, payload: PersonaPayload) -> dict:
    """Edit an existing persona in place."""
    data = payload.model_dump(exclude={"key"}, exclude_unset=True)
    updated = persona_store.update_persona(key, data)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"persona '{key}' not found")
    return updated


@app.delete("/api/personas/catalog/{key}", status_code=204)
async def delete_persona_template(key: str) -> Response:
    """Remove a persona from the catalog."""
    if not persona_store.delete_persona(key):
        raise HTTPException(status_code=404, detail=f"persona '{key}' not found")
    return Response(status_code=204)



def _personas_from_param(raw: str | None) -> list[PlayerPersona] | None:
    """Parse a UI-supplied roster (URL JSON) into validated personas.

    Ids are reassigned to a stable ``player_1..player_n`` sequence so a custom
    cast can never collide or leave gaps. Returns None when no override is given
    or the payload is unusable, in which case the authored roster is kept.
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list) or not data:
        return None

    personas: list[PlayerPersona] = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            continue
        payload = {**entry, "id": f"player_{len(personas) + 1}"}
        try:
            personas.append(PlayerPersona.model_validate(payload))
        except Exception:
            continue
    return personas or None


@app.websocket("/ws/game")
async def game_socket(websocket: WebSocket) -> None:
    await websocket.accept()

    model = websocket.query_params.get("model", DEFAULT_MODEL)
    try:
        rounds = int(websocket.query_params.get("rounds", "30"))
    except ValueError:
        rounds = 30
    # Narration on by default; pass narrate=false to disable the storyteller.
    narrate = websocket.query_params.get("narrate", "true").lower() != "false"

    setting = current_setting()

    # Optional UI-configured roster (add/edit personas + per-player models).
    custom_personas = _personas_from_param(websocket.query_params.get("personas"))
    if custom_personas:
        setting.players = custom_personas

    async def send(message: dict) -> None:
        await websocket.send_text(json.dumps(message))

    try:
        # Re-announce setup so the UI reflects the roster actually in play.
        agents = build_agents(setting, model=model)
        narrator = build_narrator(model=model) if narrate else None
        runner = GameRunner(setting, agents, max_rounds=rounds, narrator=narrator)
        await runner.run(send)
    except WebSocketDisconnect:
        return
    except Exception as exc:  # surface runtime errors (e.g. Ollama unreachable)
        await send({"type": "error", "message": str(exc)})
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass
