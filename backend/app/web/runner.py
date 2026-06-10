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

from pathlib import Path
from typing import Awaitable, Callable, Optional

from app.agents.game_player_agent import GamePlayerAgent
from app.agents.gm_narrator import GameMasterNarrator
from app.agents.storyboard import Storyboard, is_storyboard_stale, storyboard_path_for
from app.agents.storyboard_generator import StoryboardGenerator, make_storyboard_client
from app.context.channels import Event, EventKind
from app.llm.ollama_client import OllamaClient
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

    Human personas (``is_human=True``) receive no LLM client — the orchestrator
    pauses and waits for real user input instead of calling the model.
    """
    if not setting.players:
        raise ValueError("This setting defines no players to drive.")

    clients: dict[str, OllamaClient] = {}

    def client_for(persona_model: str) -> OllamaClient:
        if persona_model not in clients:
            clients[persona_model] = (
                OllamaClient(model=persona_model, base_url=base_url)
                if base_url
                else OllamaClient(model=persona_model)
            )
        return clients[persona_model]

    agents: list[GamePlayerAgent] = []
    for persona in setting.players:
        if persona.is_human:
            agents.append(GamePlayerAgent(persona=persona, client=None))
            continue
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


async def load_or_generate_storyboard(
    setting: GameSetting,
    world_path: Optional[Path],
    base_url: Optional[str] = None,
) -> Storyboard:
    """Return a Storyboard for this world, auto-generating if needed.

    If world_path is None or generation fails, returns an empty Storyboard
    so the game can always proceed without narrative context.
    """
    if world_path is None:
        return Storyboard()

    sb_path = storyboard_path_for(world_path)
    world_id = world_path.stem

    if not is_storyboard_stale(world_path, sb_path):
        try:
            return Storyboard.from_file(sb_path)
        except Exception as exc:
            print(f"[runner] Failed to load storyboard from {sb_path}: {exc}")

    # Missing or stale — generate now.
    print(f"[runner] Generating storyboard for {world_id} (this may take ~60s)...")
    client = make_storyboard_client(base_url)
    generator = StoryboardGenerator(client)
    storyboard = await generator.generate(setting, world_id=world_id)

    if not storyboard.is_empty():
        try:
            storyboard.save(sb_path)
            print(f"[runner] Storyboard saved to {sb_path.name}")
        except Exception as exc:
            print(f"[runner] Could not save storyboard: {exc}")
    else:
        print(f"[runner] Storyboard generation returned empty — continuing without it.")

    return storyboard


def build_narrator(
    *,
    model: str = DEFAULT_MODEL,
    base_url: Optional[str] = None,
    temperature: float = 0.8,
    storyboard: Optional[Storyboard] = None,
) -> GameMasterNarrator:
    """Construct the GM storyteller bound to a local Ollama model."""
    client = (
        OllamaClient(model=model, base_url=base_url)
        if base_url
        else OllamaClient(model=model)
    )
    return GameMasterNarrator(
        client=client,
        temperature=temperature,
        storyboard=storyboard or Storyboard(),
    )


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
        self._orchestrator: GameOrchestrator | None = None

    # ------------------------------------------------------------------ #
    # Human-interaction API — delegate to the live orchestrator
    # ------------------------------------------------------------------ #
    def inject_nudge(self, text: str) -> None:
        """Forward a human observer's hint to the running orchestrator."""
        if self._orchestrator is not None:
            self._orchestrator.inject_nudge(text)

    def submit_human_action(self, action_dict: dict) -> None:
        """Forward a human player's chosen action to the running orchestrator."""
        if self._orchestrator is not None:
            self._orchestrator.submit_human_action(action_dict)

    def submit_deduction(self, answer: str) -> None:
        """Forward the human's final deduction answer to the running orchestrator."""
        if self._orchestrator is not None:
            self._orchestrator.submit_deduction(answer)

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
        self._orchestrator = orchestrator

        # Initial snapshot before any turn.
        await send(state_snapshot(orchestrator.sim.state))

        result = await orchestrator.run()
        await send(result_to_dict(result))
        return result
