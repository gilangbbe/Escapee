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
from app.schemas.fixed_world import load_setting_compat
from app.schemas.game_setting import GameSetting
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

    async def send(message: dict) -> None:
        await websocket.send_text(json.dumps(message))

    try:
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
