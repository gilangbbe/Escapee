"""
GameRunner — wires a `GameSetting` into a live, streamed game for the web layer.

It builds one `GamePlayerAgent` per persona (each bound to a local Ollama model),
constructs the `GameOrchestrator`, and runs it while streaming JSON messages to a
caller-supplied async `send` callback. After every event it also pushes a fresh
grounded `state` snapshot so the UI's state panel stays in sync, and a final
`result` message when the game ends.

The simulator remains the single source of truth; this module only formats and
streams what the orchestrator produces.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from app.agents.game_player_agent import GamePlayerAgent
from app.agents.gm_narrator import GameMasterNarrator
from app.context.channels import Event, EventKind
from app.llm.llama_cpp_client import LlamaCppClient, get_model_path
from app.llm.ollama_client import OllamaClient

# If MODEL_PATH env var is set, use llama-cpp-python. Otherwise use Ollama.
_MODEL_PATH = get_model_path()


def _make_client(model: str, base_url: str | None = None) -> OllamaClient | LlamaCppClient:
    """Return the appropriate LLM client based on environment config."""
    if _MODEL_PATH:
        return LlamaCppClient(model_path=_MODEL_PATH)
    if base_url:
        return OllamaClient(model=model, base_url=base_url)
    return OllamaClient(model=model)
from app.orchestrator.loop import GameOrchestrator, GameResult
from app.schemas.game_setting import GameSetting
from app.web.serializers import (
    event_to_dict,
    result_to_dict,
    setup_message,
    state_snapshot,
)

# Async sink the runner pushes JSON-able dicts into (e.g. a WebSocket sender).
SendFn = Callable[[dict], Awaitable[None]]

DEFAULT_MODEL = "qwen2.5:7b"


def build_agents(
    setting: GameSetting,
    *,
    model: str = DEFAULT_MODEL,
    base_url: Optional[str] = None,
    temperature: float = 0.4,
) -> list[GamePlayerAgent]:
    """Construct one agent per persona, honoring per-persona LLM bindings.

    A persona may declare its own ``model``/``temperature`` (see the persona
    catalog) so a single team can mix models. When a persona omits them, the
    ``model``/``temperature`` arguments here are the team-wide fallback. One
    `OllamaClient` is shared per distinct ``(model, base_url)`` pair so multiple
    personas on the same model do not spin up redundant clients.
    """
    if not setting.players:
        raise ValueError("This setting defines no players to drive.")

    clients: dict[str, OllamaClient | LlamaCppClient] = {}

    def client_for(persona_model: str) -> OllamaClient | LlamaCppClient:
        if persona_model not in clients:
            clients[persona_model] = _make_client(persona_model, base_url)
        return clients[persona_model]

    agents: list[GamePlayerAgent] = []
    for persona in setting.players:
        persona_model = persona.model or model
        persona_temp = persona.temperature if persona.temperature is not None else temperature
        agents.append(
            GamePlayerAgent(
                persona=persona,
                client=client_for(persona_model),
                temperature=persona_temp,
            )
        )
    return agents


def build_narrator(
    *,
    model: str = DEFAULT_MODEL,
    base_url: Optional[str] = None,
    temperature: float = 0.8,
) -> GameMasterNarrator:
    """Construct the GM storyteller bound to a local Ollama model."""
    client = _make_client(model, base_url)
    return GameMasterNarrator(client=client, temperature=temperature)


class GameRunner:
    """Runs a streamed game, pushing setup/event/state/result messages to `send`."""

    def __init__(
        self,
        setting: GameSetting,
        agents: list[GamePlayerAgent],
        *,
        max_rounds: int = 30,
        narrator: Optional[GameMasterNarrator] = None,
        enforce_candidate_policy: bool = False,
    ) -> None:
        self.setting = setting
        self.agents = agents
        self.max_rounds = max_rounds
        self.narrator = narrator
        self.enforce_candidate_policy = enforce_candidate_policy

    async def run(self, send: SendFn) -> GameResult:
        """Stream the full game over `send` and return the final result."""
        await send(setup_message(self.setting))

        async def on_event(event: Event) -> None:
            # PLANNER events are internal telemetry — hide from UI.
            # Progress milestones use EventKind.SYSTEM, so they still show.
            if event.kind == EventKind.PLANNER:
                return
            await send(event_to_dict(event))
            # Push a grounded snapshot after every event so the panel tracks state.
            await send(state_snapshot(orchestrator.sim.state))

        orchestrator = GameOrchestrator(
            self.setting,
            self.agents,
            max_rounds=self.max_rounds,
            on_event=on_event,
            narrator=self.narrator,
            enforce_candidate_policy=self.enforce_candidate_policy,
        )

        # Initial snapshot before any turn.
        await send(state_snapshot(orchestrator.sim.state))

        result = await orchestrator.run()
        await send(result_to_dict(result))
        return result
