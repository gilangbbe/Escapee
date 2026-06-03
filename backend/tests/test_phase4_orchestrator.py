"""Tests for the v2 player agents (GameSetting) and the Phase 4 orchestrator."""

from __future__ import annotations

import json

import pytest

from app.agents.game_player_agent import GamePlayerAgent
from app.agents.game_player_prompt import build_player_system_prompt, primary_objective
from app.agents.game_turn import GameTurn
from app.context.channels import Event, EventKind, MessageLog
from app.context.game_builder import build_game_state_view, seed_private_clues
from app.engine.game_simulator import GameSimulator
from app.game.orbital_decay_setting import ORBITAL_DECAY_SETTING
from app.llm.json_repair import parse_and_validate
from app.orchestrator.loop import GameOrchestrator
from app.schemas.game_setting import GameSetting


@pytest.fixture
def setting() -> GameSetting:
    return GameSetting.model_validate(ORBITAL_DECAY_SETTING)


@pytest.fixture
def sim(setting: GameSetting) -> GameSimulator:
    return GameSimulator(setting)


# --------------------------------------------------------------------------- #
# Fake LLM client (scripted)
# --------------------------------------------------------------------------- #
class ScriptedClient:
    """Returns pre-baked JSON strings in order, ignoring the prompt."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, messages, *, json_mode: bool = False, format_schema: dict | None = None, temperature: float = 0.7) -> str:
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        # Default filler: a harmless say.
        return json.dumps(
            {"thought": "waiting", "speak": None, "action": {"action": "say", "message": "..."}}
        )


def turn_json(
    action: dict,
    *,
    thought: str = "thinking",
    speak: str | None = None,
    hypothesis: str | None = None,
) -> str:
    return json.dumps(
        {"thought": thought, "hypothesis": hypothesis, "speak": speak, "action": action}
    )


# --------------------------------------------------------------------------- #
# State view (grounding)
# --------------------------------------------------------------------------- #
def test_state_view_only_lists_visible_objects(sim: GameSimulator):
    view = build_game_state_view(sim.state, "player_1")
    assert "supply_locker" in view.visible_objects
    assert "cryo_pod" in view.visible_objects
    # power_cell is hidden and in another room.
    assert "power_cell" not in view.visible_objects
    assert "nav_console" not in view.visible_objects


def test_state_view_render_uses_bracketed_ids(sim: GameSimulator):
    text = build_game_state_view(sim.state, "player_1").render()
    assert "[supply_locker]" in text
    assert "OTHER PLAYERS HERE" in text


def test_state_view_reflects_inventory(sim: GameSimulator):
    from app.engine.game_actions import GameAction, GameActionType as A

    sim.step("player_1", GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"))
    sim.step("player_1", GameAction(action=A.TAKE, target_id="access_card"))
    view = build_game_state_view(sim.state, "player_1")
    assert "access_card" in view.inventory


# --------------------------------------------------------------------------- #
# Prompt / persona re-pinning
# --------------------------------------------------------------------------- #
def test_system_prompt_repins_persona(setting: GameSetting):
    persona = setting.players[0]
    prompt = build_player_system_prompt(persona)
    assert persona.name in prompt
    assert persona.role in prompt
    # The action grammar is re-pinned every turn as a plain semantic cheatsheet
    # (structure itself is enforced by constrained decoding, not prompt text).
    assert "enter_code" in prompt
    assert "set_fuse" in prompt


def test_primary_objective_uses_setting(setting: GameSetting):
    assert primary_objective(setting) == setting.objective


# --------------------------------------------------------------------------- #
# ReAct parsing
# --------------------------------------------------------------------------- #
def test_game_turn_parses_from_model_text():
    raw = turn_json({"action": "inspect", "target_id": "captains_log"}, speak="On it.")
    result = parse_and_validate(raw, GameTurn)
    assert result.ok
    assert result.value.action.target_id == "captains_log"
    assert result.value.speak == "On it."


# --------------------------------------------------------------------------- #
# Agent build_messages includes grounded state + private clue
# --------------------------------------------------------------------------- #
def test_agent_messages_include_state_and_clue(setting: GameSetting):
    sim = GameSimulator(setting)
    log = MessageLog()
    seed_private_clues(log, sim.state)
    agent = GamePlayerAgent(persona=setting.players[0], client=ScriptedClient([]))

    messages = agent.build_messages(sim.state, log)
    user = messages[1]["content"]
    assert "[supply_locker]" in user
    assert "captain" in user.lower()  # player_1's clue mentions the captain's log


@pytest.mark.asyncio
async def test_agent_decide_returns_validated_turn(setting: GameSetting):
    sim = GameSimulator(setting)
    log = MessageLog()
    client = ScriptedClient(
        [turn_json({"action": "inspect", "target_id": "captains_log"})]
    )
    agent = GamePlayerAgent(persona=setting.players[0], client=client)
    result = await agent.decide(sim.state, log)
    assert result.ok
    assert result.value.action.target_id == "captains_log"


@pytest.mark.asyncio
async def test_agent_repairs_missing_use_target_from_grounded_state(setting: GameSetting):
    from app.engine.game_actions import GameAction, GameActionType as A

    sim = GameSimulator(setting)
    log = MessageLog()
    seed_private_clues(log, sim.state)

    # Make access_card available so the intended USE target is unambiguous.
    sim.step("player_1", GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"))
    sim.step("player_1", GameAction(action=A.TAKE, target_id="access_card"))

    client = ScriptedClient(
        [
            turn_json(
                {
                    "action": "use",
                    "item_id": "access_card",
                    "target_id": None,
                }
            )
        ]
    )
    agent = GamePlayerAgent(persona=setting.players[0], client=client)
    orch = GameOrchestrator(setting, [agent, GamePlayerAgent(persona=setting.players[1], client=ScriptedClient([]))], max_rounds=1, enable_planning=False)

    result = await agent.decide(sim.state, log, team_brief=orch.cognition.brief_for("player_1", sim.state))
    assert result.ok
    assert result.value.action.action == A.USE
    assert result.value.action.target_id == "command_door"


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def _winning_scripts() -> tuple[list[str], list[str]]:
    """Per-player scripts that drive a full cooperative win (round-robin p1,p2)."""
    p1 = [
        turn_json({"action": "inspect", "target_id": "captains_log"}, speak="Reading the log."),
        turn_json({"action": "enter_code", "target_id": "supply_locker", "code": "0451"}),
        turn_json({"action": "take", "target_id": "access_card"}),
        turn_json({"action": "use", "item_id": "access_card", "target_id": "command_door"},
                  speak="Door's open!"),
        turn_json({"action": "move", "to_room": "command_deck"}),
    ]
    p2 = [
        turn_json({"action": "look"}),
        turn_json({"action": "say", "message": "Standing by."}),
        turn_json({"action": "look"}),
        turn_json({"action": "say", "message": "Ready when you are."}),
        turn_json({"action": "move", "to_room": "command_deck"}),
        turn_json({"action": "enter_code", "target_id": "nav_console", "code": "reroute auxiliary"}),
        turn_json({"action": "take", "target_id": "power_cell"}),
        turn_json({"action": "use", "item_id": "power_cell", "target_id": "power_panel"},
                  speak="Powering the airlock!"),
    ]
    return p1, p2


@pytest.mark.asyncio
async def test_orchestrator_runs_team_to_victory(setting: GameSetting):
    p1_script, p2_script = _winning_scripts()
    agents = [
        GamePlayerAgent(persona=setting.players[0], client=ScriptedClient(p1_script)),
        GamePlayerAgent(persona=setting.players[1], client=ScriptedClient(p2_script)),
    ]

    streamed: list[Event] = []

    async def sink(e: Event) -> None:
        streamed.append(e)

    orch = GameOrchestrator(
        setting,
        agents,
        max_rounds=12,
        on_event=sink,
        enable_planning=False,
        enforce_candidate_policy=False,
    )
    result = await orch.run()

    assert result.won is True
    assert result.reason == "escaped"
    assert orch.sim.state.object_state["airlock_door"].value == "unlocked"
    # The stream carried both speech and observation events.
    kinds = {e.kind for e in streamed}
    assert EventKind.SPEECH in kinds
    assert EventKind.OBSERVATION in kinds


@pytest.mark.asyncio
async def test_orchestrator_seeds_private_clues(setting: GameSetting):
    agents = [
        GamePlayerAgent(persona=setting.players[0], client=ScriptedClient([])),
        GamePlayerAgent(persona=setting.players[1], client=ScriptedClient([])),
    ]
    orch = GameOrchestrator(setting, agents, max_rounds=1)
    assert orch.log.private_clues["player_1"]
    assert orch.log.private_clues["player_2"]
    # Privacy: player_1 must not see player_2's private clue events.
    p2_clue_event = Event(
        kind=EventKind.SYSTEM, actor_id=None, text="secret", turn=0,
        public=False, audience_id="player_2",
    )
    orch.log.add(p2_clue_event)
    assert p2_clue_event not in orch.log.visible_to("player_1")


@pytest.mark.asyncio
async def test_orchestrator_stops_at_turn_limit(setting: GameSetting):
    # Agents that only ever idle never win -> hit the turn limit.
    idlers = [
        GamePlayerAgent(persona=setting.players[0], client=ScriptedClient([])),
        GamePlayerAgent(persona=setting.players[1], client=ScriptedClient([])),
    ]
    orch = GameOrchestrator(setting, idlers, max_rounds=3, enable_planning=False)
    result = await orch.run()
    assert result.won is False
    assert result.reason == "turn_limit"


@pytest.mark.asyncio
async def test_orchestrator_uses_planner_override_when_stuck_on_free_actions(setting: GameSetting):
    # Repeated LOOK actions create no progress. With a low stall threshold,
    # the orchestrator should escalate and override via planner-as-tool.
    script = [
        turn_json({"action": "look"}),
        turn_json({"action": "look"}),
        turn_json({"action": "look"}),
        turn_json({"action": "look"}),
    ]
    agent = GamePlayerAgent(persona=setting.players[0], client=ScriptedClient(script))

    streamed: list[Event] = []

    async def sink(e: Event) -> None:
        streamed.append(e)

    orch = GameOrchestrator(
        setting,
        [agent],
        max_rounds=3,
        on_event=sink,
        enable_planning=False,
        stall_threshold=1,
        enforce_candidate_policy=True,
        max_redecide=1,
    )
    await orch.run()

    assert any(
        e.kind == EventKind.SYSTEM and "(planner override)" in e.text
        for e in streamed
    )
    planner_events = [e for e in streamed if e.kind == EventKind.PLANNER]
    assert planner_events
    assert any(
        e.data and isinstance(e.data.get("candidates"), list) and e.data.get("chosen")
        for e in planner_events
    )


@pytest.mark.asyncio
async def test_invalid_turn_falls_back_to_look_instead_of_dead_end(setting: GameSetting):
    # Missing target_id for USE reproduces the reported validation failure.
    invalid_use = json.dumps(
        {
            "reflection": None,
            "hypothesis": None,
            "speak": None,
            "action": {
                "action": "use",
                "item_id": "rusty_key_ring",
                "target_id": None,
                "message": "hint has a hidden code.",
            },
        }
    )
    # Force invalid output on every internal retry so decide() ultimately fails.
    agent = GamePlayerAgent(
        persona=setting.players[0],
        client=ScriptedClient([invalid_use, invalid_use, invalid_use, invalid_use]),
    )
    # A passive teammate so turn order remains valid.
    teammate = GamePlayerAgent(persona=setting.players[1], client=ScriptedClient([]))

    streamed: list[Event] = []

    async def sink(e: Event) -> None:
        streamed.append(e)

    orch = GameOrchestrator(setting, [agent, teammate], max_rounds=1, on_event=sink, enable_planning=False)
    won = await orch._take_turn(agent)

    assert won is False
    assert any(
        e.kind == EventKind.SYSTEM and "hesitated" in e.text and "target_id" in e.text
        for e in streamed
    )
    # Recovery path emitted an observation from fallback LOOK.
    assert any(
        e.kind == EventKind.OBSERVATION and "You are in" in e.text
        for e in streamed
    )


def test_orchestrator_rejects_unknown_persona(setting: GameSetting):
    from app.schemas.game_setting import PlayerPersona

    bogus = GamePlayerAgent(
        persona=PlayerPersona(id="ghost", name="Ghost", role="phantom"),
        client=ScriptedClient([]),
    )
    with pytest.raises(ValueError):
        GameOrchestrator(setting, [bogus])


# --------------------------------------------------------------------------- #
# GM Narrator (presentation layer)
# --------------------------------------------------------------------------- #
class NarratingClient:
    """Fake narrator LLM: returns a tagged line so we can spot its output."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, *, json_mode: bool = False, format_schema: dict | None = None, temperature: float = 0.7) -> str:
        self.calls += 1
        return f"STORY#{self.calls}"


@pytest.mark.asyncio
async def test_narration_streams_but_never_enters_message_log(setting: GameSetting):
    from app.agents.gm_narrator import GameMasterNarrator

    p1_script, p2_script = _winning_scripts()
    agents = [
        GamePlayerAgent(persona=setting.players[0], client=ScriptedClient(p1_script)),
        GamePlayerAgent(persona=setting.players[1], client=ScriptedClient(p2_script)),
    ]
    narrator = GameMasterNarrator(client=NarratingClient())  # type: ignore[arg-type]

    streamed: list[Event] = []

    async def sink(e: Event) -> None:
        streamed.append(e)

    orch = GameOrchestrator(
        setting,
        agents,
        max_rounds=12,
        on_event=sink,
        narrator=narrator,
        enable_planning=False,
        enforce_candidate_policy=False,
    )
    result = await orch.run()

    assert result.won is True

    # Narration reached observers via the stream...
    narration = [e for e in streamed if e.kind == EventKind.NARRATION]
    assert narration, "expected narration events in the stream"
    assert all(e.text.startswith("STORY#") for e in narration)
    # ...including an opening (turn 0) and an ending narration.
    assert any(e.turn == 0 for e in narration)

    # ...but NONE of it was recorded into the shared MessageLog (anti-leak).
    logged_kinds = {e.kind for e in orch.log.events}
    assert EventKind.NARRATION not in logged_kinds


# --------------------------------------------------------------------------- #
# Multi-agent collaboration + external cognition (memory / loop / progress)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_hypothesis_is_broadcast_to_the_team(setting: GameSetting):
    p1_script = [
        turn_json(
            {"action": "look"},
            hypothesis="I think the captain's log holds the locker code.",
            speak="Let's check the log first.",
        )
    ]
    agents = [
        GamePlayerAgent(persona=setting.players[0], client=ScriptedClient(p1_script)),
        GamePlayerAgent(persona=setting.players[1], client=ScriptedClient([])),
    ]
    streamed: list[Event] = []

    async def sink(e: Event) -> None:
        streamed.append(e)

    orch = GameOrchestrator(
        setting, agents, max_rounds=1, on_event=sink, enable_planning=False
    )
    await orch.run()

    # The proposal is on the blackboard and was broadcast as public speech.
    assert "captain" in orch.cognition.hypotheses["player_1"].lower()
    idea_events = [
        e for e in streamed if e.kind == EventKind.SPEECH and e.text.startswith("(idea)")
    ]
    assert idea_events, "expected the hypothesis to be broadcast as public speech"


@pytest.mark.asyncio
async def test_failed_action_is_remembered_in_episodic_memory(setting: GameSetting):
    # A valid-but-wrong code: passes schema validation, fails in the sim.
    p1_script = [turn_json({"action": "enter_code", "target_id": "supply_locker", "code": "9999"})]
    agents = [
        GamePlayerAgent(persona=setting.players[0], client=ScriptedClient(p1_script)),
        GamePlayerAgent(persona=setting.players[1], client=ScriptedClient([])),
    ]
    orch = GameOrchestrator(setting, agents, max_rounds=1, enable_planning=False)
    await orch.run()

    failed = [a.signature for a in orch.cognition.attempts if not a.success]
    assert any("enter_code" in sig and "supply_locker" in sig for sig in failed)


@pytest.mark.asyncio
async def test_redundant_action_is_blocked_and_redecided(setting: GameSetting):
    # Same wrong code twice: the 2nd is a proven no-op (world unchanged) and must
    # be blocked + re-decided into a different action.
    p1_script = [
        turn_json({"action": "enter_code", "target_id": "supply_locker", "code": "9999"}),
        turn_json({"action": "enter_code", "target_id": "supply_locker", "code": "9999"}),
        turn_json({"action": "say", "message": "let me rethink"}),
    ]
    agents = [
        GamePlayerAgent(persona=setting.players[0], client=ScriptedClient(p1_script)),
        GamePlayerAgent(persona=setting.players[1], client=ScriptedClient([])),
    ]
    streamed: list[Event] = []

    async def sink(e: Event) -> None:
        streamed.append(e)

    orch = GameOrchestrator(setting, agents, max_rounds=3, on_event=sink)
    await orch._take_turn(agents[0])  # enter_code 9999 -> fail, recorded
    await orch._take_turn(agents[0])  # enter_code 9999 again -> blocked -> redecide to say

    loop_events = [
        e for e in streamed if e.kind == EventKind.SYSTEM and "loop avoided" in e.text.lower()
    ]
    assert loop_events, "expected a 'loop avoided' block when repeating a dead-end"


@pytest.mark.asyncio
async def test_progress_milestone_is_announced(setting: GameSetting):
    # Inspecting the captain's log discovers info -> a milestone -> a reward signal.
    p1_script = [turn_json({"action": "inspect", "target_id": "captains_log"})]
    agents = [
        GamePlayerAgent(persona=setting.players[0], client=ScriptedClient(p1_script)),
        GamePlayerAgent(persona=setting.players[1], client=ScriptedClient([])),
    ]
    streamed: list[Event] = []

    async def sink(e: Event) -> None:
        streamed.append(e)

    orch = GameOrchestrator(setting, agents, max_rounds=1, on_event=sink)
    await orch._take_turn(agents[0])

    progress = [
        e for e in streamed if e.kind == EventKind.SYSTEM and e.text.startswith("✓ Progress")
    ]
    assert progress, "expected a progress milestone announcement"


@pytest.mark.asyncio
async def test_team_is_flagged_stuck_after_no_progress(setting: GameSetting):
    # Idlers keep emitting the default 'say' -> never reach a milestone.
    idlers = [
        GamePlayerAgent(persona=setting.players[0], client=ScriptedClient([])),
        GamePlayerAgent(persona=setting.players[1], client=ScriptedClient([])),
    ]
    streamed: list[Event] = []

    async def sink(e: Event) -> None:
        streamed.append(e)

    orch = GameOrchestrator(
        setting, idlers, max_rounds=8, on_event=sink, stall_threshold=4,
        enable_planning=False,
        enable_planner_tool=False,
    )
    await orch.run()

    assert orch.cognition.stuck is True
    stuck_events = [
        e for e in streamed if e.kind == EventKind.SYSTEM and "stuck" in e.text.lower()
    ]
    assert stuck_events, "expected a 'team is stuck' system prompt to be emitted"


@pytest.mark.asyncio
async def test_stuck_flag_clears_after_real_progress(setting: GameSetting):
    # 'say' makes no milestone progress; a novel info discovery resets the flag.
    p1_script = [
        turn_json({"action": "say", "message": "hmm"}),   # no progress -> counter 1
        turn_json({"action": "say", "message": "hmm2"}),  # no progress -> counter 2 -> stuck
        turn_json({"action": "inspect", "target_id": "captains_log"}),  # milestone -> reset
    ]
    agents = [
        GamePlayerAgent(persona=setting.players[0], client=ScriptedClient(p1_script)),
        GamePlayerAgent(persona=setting.players[1], client=ScriptedClient([])),
    ]
    orch = GameOrchestrator(setting, agents, max_rounds=3, stall_threshold=2)
    await orch._take_turn(agents[0])  # say -> counter 1
    await orch._take_turn(agents[0])  # say -> counter 2 -> stuck
    assert orch.cognition.stuck is True
    await orch._take_turn(agents[0])  # inspect captains_log -> milestone -> unstuck
    assert orch.cognition.stuck is False


# --------------------------------------------------------------------------- #
# External cognition unit checks
# --------------------------------------------------------------------------- #
def test_world_fingerprint_changes_only_on_state_change(setting: GameSetting):
    from app.cognition.team_cognition import world_fingerprint
    from app.engine.game_actions import GameAction, GameActionType as A

    sim = GameSimulator(setting)
    fp0 = world_fingerprint(sim.state)
    # A failed action leaves the world (and fingerprint) unchanged.
    sim.step("player_1", GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0000"))
    assert world_fingerprint(sim.state) == fp0
    # A successful action that opens the locker changes the fingerprint.
    sim.step("player_1", GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"))
    assert world_fingerprint(sim.state) != fp0


def test_reflection_is_scheduled_every_n_turns():
    from app.cognition.team_cognition import CognitionConfig, TeamCognition

    cog = TeamCognition(config=CognitionConfig(reflect_every=3))
    assert cog.should_reflect(0) is False
    assert cog.should_reflect(2) is False
    assert cog.should_reflect(3) is True
    assert cog.should_reflect(6) is True


# --------------------------------------------------------------------------- #
# Phase 10: objective board + goal-level no-repeat + collaborative planning
# --------------------------------------------------------------------------- #
def test_derive_board_lists_solved_items_and_open_win_condition(setting: GameSetting):
    from app.cognition.team_cognition import derive_board
    from app.engine.game_actions import GameAction, GameActionType as A

    sim = GameSimulator(setting)
    # Open the supply locker so it becomes a SOLVED objective.
    sim.step("player_1", GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"))

    board = derive_board(sim.state)
    assert any("supply_locker" in s for s in board.solved)
    # The win condition (escape) is not met yet -> it appears as still-to-do.
    assert any("WIN CONDITION" in u for u in board.unsolved)
    assert board.objective  # the room's objective string is carried through


def test_derive_board_treats_open_as_satisfying_unlocked_win_condition(setting: GameSetting):
    from app.cognition.team_cognition import derive_board
    from app.schemas.game_setting import ObjectState

    sim = GameSimulator(setting)
    # Win target in this setting is UNLOCKED; OPEN should count as satisfied too.
    sim.state.object_state["airlock_door"] = ObjectState.OPEN

    board = derive_board(sim.state)
    assert not any("WIN CONDITION" in u for u in board.unsolved)


def test_already_done_blocks_completed_goals(setting: GameSetting):
    from app.cognition.team_cognition import TeamCognition
    from app.engine.game_actions import GameAction, GameActionType as A

    sim = GameSimulator(setting)
    cog = TeamCognition()

    # Re-entering a code on an already-opened object is a completed goal.
    sim.step("player_1", GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"))
    note = cog.already_done(
        "player_1",
        GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"),
        sim.state,
    )
    assert note is not None
    # A free action (look) is never blocked as "already done".
    assert cog.already_done("player_1", GameAction(action=A.LOOK), sim.state) is None


def test_blocked_reason_ignores_free_actions_and_catches_completed_goals(setting: GameSetting):
    from app.cognition.team_cognition import TeamCognition, world_fingerprint
    from app.engine.game_actions import GameAction, GameActionType as A

    sim = GameSimulator(setting)
    cog = TeamCognition()

    # LOOK is free -> never blocked.
    wk = world_fingerprint(sim.state)
    assert cog.blocked_reason("player_1", GameAction(action=A.LOOK), wk, sim.state) is None

    # After opening the locker, re-entering the code is blocked as already done.
    sim.step("player_1", GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"))
    wk2 = world_fingerprint(sim.state)
    note = cog.blocked_reason(
        "player_1",
        GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"),
        wk2,
        sim.state,
    )
    assert note is not None
    assert "ALREADY DONE" in note


def test_team_plan_refresh_and_render():
    from app.cognition.team_cognition import TeamCognition

    cog = TeamCognition()
    cog.set_plan(["Open the supply_locker", "Use the access_card on the door"])
    assert len(cog.plan.steps) == 2
    assert all(not s.done for s in cog.plan.steps)

    cog.plan.refresh({"supply_locker"})
    rendered = cog.plan.render()
    assert "[x]" in rendered  # the locker step is now ticked
    assert "[ ]" in rendered  # the access_card step is still open


def test_brief_includes_next_open_plan_step(setting: GameSetting):
    from app.cognition.team_cognition import TeamCognition
    from app.engine.game_actions import GameAction, GameActionType as A
    from app.engine.game_simulator import GameSimulator

    sim = GameSimulator(setting)
    cog = TeamCognition()
    cog.set_plan([
        "enter_code supply_locker 0451",
        "use access_card on command_door",
    ])

    # Complete the first step so the second becomes the active plan step.
    sim.step("player_1", GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"))
    brief = cog.brief_for("player_1", sim.state)
    assert "access_card" in brief.next_plan_step


def test_candidate_actions_prune_invalid_use_targets(setting: GameSetting):
    from app.cognition.team_cognition import TeamCognition
    from app.engine.game_actions import GameAction, GameActionType as A
    from app.engine.game_simulator import GameSimulator

    sim = GameSimulator(setting)
    cog = TeamCognition()

    # Reach state with access_card in inventory while still in cryo_bay.
    sim.step("player_1", GameAction(action=A.ENTER_CODE, target_id="supply_locker", code="0451"))
    sim.step("player_1", GameAction(action=A.TAKE, target_id="access_card"))

    brief = cog.brief_for("player_1", sim.state)
    actions = "\n".join(brief.candidate_actions)
    assert "use access_card on command_door" in actions
    assert "use access_card on cryo_pod" not in actions
    assert "use access_card on supply_locker" not in actions


@pytest.mark.asyncio
async def test_planning_phase_populates_shared_plan(setting: GameSetting):
    # The first turn of each agent answers the planning debate (PlanProposal JSON).
    plan_p1 = json.dumps({"thought": "draft", "plan": ["read the log", "open the locker"]})
    plan_p2 = json.dumps(
        {"thought": "refine", "plan": ["read the log", "open the locker", "escape"]}
    )
    agents = [
        GamePlayerAgent(persona=setting.players[0], client=ScriptedClient([plan_p1])),
        GamePlayerAgent(persona=setting.players[1], client=ScriptedClient([plan_p2])),
    ]
    streamed: list[Event] = []

    async def sink(e: Event) -> None:
        streamed.append(e)

    orch = GameOrchestrator(setting, agents, max_rounds=1, on_event=sink)
    await orch._plan_phase()

    assert [s.text for s in orch.cognition.plan.steps] == [
        "read the log",
        "open the locker",
        "escape",
    ]
    plan_events = [
        e for e in streamed if e.kind == EventKind.SYSTEM and "Shared plan" in e.text
    ]
    assert plan_events, "expected the agreed shared plan to be announced"


@pytest.mark.asyncio
async def test_planning_phase_falls_back_to_solution_path(setting: GameSetting):
    # Agents that never return a plan (empty scripts -> default 'say') -> fallback.
    agents = [
        GamePlayerAgent(persona=setting.players[0], client=ScriptedClient([])),
        GamePlayerAgent(persona=setting.players[1], client=ScriptedClient([])),
    ]
    orch = GameOrchestrator(setting, agents, max_rounds=1)
    await orch._plan_phase()
    assert [s.text for s in orch.cognition.plan.steps] == list(setting.solution_path)


