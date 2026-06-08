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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.game.default_story import DEFAULT_STORY_PAYLOAD
from app.game.personas import catalog_as_dicts
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
    """Persona-editor bootstrap data for the UI.

    Returns the current default roster (so the editor opens pre-filled), the
    catalog of reusable persona templates, and the list of models installed on
    the local Ollama server (best-effort; empty if Ollama is unreachable).
    """
    setting = current_setting()
    installed = await list_installed_models(DEFAULT_OLLAMA_URL)
    # Always include the configured default so it is selectable even if the
    # tags lookup failed or that model is pulled lazily.
    models = sorted(set(installed) | {DEFAULT_MODEL})
    return {
        "default_model": DEFAULT_MODEL,
        "models": models,
        "catalog": catalog_as_dicts(),
        "roster": setup_message(setting)["players"],
    }


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
