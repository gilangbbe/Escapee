"""
Game orchestration loop (Phase 4).

The orchestrator is the conductor of a live game. It owns:
  - the deterministic `GameSimulator` (ground truth),
  - the shared `MessageLog` (public chat + per-player private clues + timeline),
  - the set of `GamePlayerAgent`s (one per persona).

Each round it walks players in turn order and runs the ReAct cycle:

    Thought → Speak → Action → Observation

For every turn it:
  1. asks the agent to `decide()` (LLM → validated `GameTurn`),
  2. records the optional public `speak` as a SPEECH event,
  3. applies the validated `GameAction` to the simulator,
  4. records the grounded `Observation` (public broadcast or private to the actor),
  5. streams both events to an optional async `on_event` sink (Phase 5 WebSocket),
  6. checks for win / turn-limit end states.

No game logic lives here beyond scheduling — the simulator stays the single
source of truth, and agents only ever influence the world through validated
actions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from app.agents.game_player_agent import GamePlayerAgent
from app.agents.gm_narrator import GameMasterNarrator
from app.agents.gm_narrator_prompt import ActionExecutionStatus, SystemEventKind, WorldSnapshot, describe_action
from app.cognition.team_cognition import (
    derive_board,
    CognitionConfig,
    TeamCognition,
    action_signature,
    describe_action_brief,
    world_fingerprint,
)
from app.cognition.action_planner import ActionPlanner
from app.context.channels import Event, EventKind, MessageLog
from app.context.game_builder import seed_private_clues
from app.engine.game_actions import GameAction, GameActionType
from app.engine.game_simulator import GameSimulator
from app.schemas.game_setting import GameSetting

# An optional sink for streaming events (e.g. a WebSocket broadcaster in Phase 5).
EventSink = Callable[[Event], Awaitable[None]]
_FREE_ACTIONS = {GameActionType.SAY, GameActionType.LOOK}
# Progress gate tuning. The gate only fires when the planner's recommended action
# is DOMINANT — both near-winning in absolute terms (a discounted win/major-unlock
# chain scores ~90+, e.g. "walk into the final room and enter the code") AND far
# better than the agent's chosen action — while the chosen action is itself
# low-value busywork. The absolute floor is what keeps the gate silent during
# ordinary mid-game exploration, where the best next step (e.g. inspecting the
# one clue object) scores only ~25-40 and must NOT be force-overridden.
_PROGRESS_GATE_DOMINANT_MIN = 90.0
_PROGRESS_GATE_GAP = 30.0
_PROGRESS_GATE_LOW_VALUE = 3.0
# Genuine puzzle attempts are never overridden: a sincere wrong try must still
# execute so the team can observe and remember the failure.
_PROGRESS_GATE_EXEMPT = {
    GameActionType.ENTER_CODE,
    GameActionType.USE,
    GameActionType.SET_FUSE,
}


@dataclass
class GameResult:
    """Outcome of a finished game run."""

    won: bool
    turns: int
    reason: str  # "escaped" | "turn_limit" | "stalled"
    events: list[Event] = field(default_factory=list)


class GameOrchestrator:
    """Drives a full multi-agent game to a win or a turn limit."""

    def __init__(
        self,
        setting: GameSetting,
        agents: list[GamePlayerAgent],
        *,
        max_rounds: int = 30,
        on_event: Optional[EventSink] = None,
        narrator: Optional[GameMasterNarrator] = None,
        stall_threshold: Optional[int] = None,
        cognition: Optional[TeamCognition] = None,
        planner: Optional[ActionPlanner] = None,
        enable_planner_tool: bool = True,
        max_redecide: int = 2,
        enable_planning: bool = True,
        enforce_candidate_policy: bool = True,
    ) -> None:
        if not agents:
            raise ValueError("At least one player agent is required.")

        self.setting = setting
        self.sim = GameSimulator(setting)
        self.log = MessageLog()
        self.max_rounds = max_rounds
        self.on_event = on_event
        self.narrator = narrator

        # Turn order follows the order agents are supplied.
        self.agents: list[GamePlayerAgent] = agents
        self._validate_agents()

        # External cognition: episodic memory, symbolic state, loop detection,
        # progress ledger, curiosity, stall + reflection scheduling, blackboard.
        # Execution-control reasoning lives HERE (deterministic), not in the LLM.
        if cognition is None:
            threshold = stall_threshold or max(3, len(agents) * 2)
            cognition = TeamCognition(
                config=CognitionConfig(stall_threshold=threshold)
            )
        elif stall_threshold is not None:
            cognition.config.stall_threshold = stall_threshold
        self.cognition = cognition
        self.enable_planner_tool = enable_planner_tool
        self.planner = planner or ActionPlanner()
        # How many times to re-ask an agent when it proposes a proven dead-end
        # before letting the (harmless) move through to keep the game flowing.
        self.max_redecide = max_redecide

        # When True, run the collaborative planning debate before round 1.
        self.enable_planning = enable_planning
        # Optional strict mode: require actions to match deterministic candidates
        # (useful for hard anti-loop experiments; disabled by default for stability).
        self.enforce_candidate_policy = enforce_candidate_policy

        # Seed each player's asymmetric private clues from the setting.
        seed_private_clues(self.log, self.sim.state)

        # Human-interaction state.
        # _pending_nudge: a hint typed by the human observer, broadcast as
        #   SYSTEM message and used as critical_note for the very next AI turn.
        # _human_action_queue: delivers GameAction dicts from the human player
        #   to _take_human_turn(), which awaits one item per turn.
        # _deduction_queue: delivers the human's final answer string during the
        #   deduction phase; one item per attempt.
        self._pending_nudge: str | None = None
        self._human_action_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._deduction_queue: asyncio.Queue[str] = asyncio.Queue()

        # Tracks which plot-critical object ids have already triggered a discovery
        # narration beat so each item only gets one connective story moment.
        self._narrated_discoveries: set[str] = set()

    # ------------------------------------------------------------------ #
    # Human-interaction public API (called by the web layer)
    # ------------------------------------------------------------------ #
    def inject_nudge(self, text: str) -> None:
        """Store a hint from the human observer.

        Emitted immediately as a SYSTEM event (visible in the UI and recorded
        in MessageLog so AI agents see it next turn) and also stored as the
        critical_note override for the very next AI agent turn.
        """
        self._pending_nudge = text.strip() or None

    def submit_human_action(self, action_dict: dict) -> None:
        """Deliver a human-player action into the turn queue."""
        self._human_action_queue.put_nowait(action_dict)

    def submit_deduction(self, answer: str) -> None:
        """Deliver the human player's final deduction answer."""
        self._deduction_queue.put_nowait(answer.strip())

    # ------------------------------------------------------------------ #
    # Setup checks
    # ------------------------------------------------------------------ #
    def _validate_agents(self) -> None:
        known = set(self.sim.state.player_locations)
        for agent in self.agents:
            if agent.persona.id not in known:
                raise ValueError(
                    f"Agent persona '{agent.persona.id}' is not a player in the game."
                )

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    async def run(self) -> GameResult:
        """Run rounds until the team escapes or the turn limit is reached."""
        await self._emit(
            EventKind.SYSTEM, None, f"The game begins. {self.setting.objective}", turn=0
        )

        # Opening scene narration (observer-only, stream-only).
        if self.narrator is not None:
            # Storyboard is pre-loaded into the narrator at construction — no generation here.
            opening = await self.narrator.narrate_opening(self.setting)
            await self._emit(EventKind.NARRATION, None, opening, turn=0, record=False)

        # Collaborative planning phase: the team debates a shared escape plan
        # BEFORE acting, so everyone shares one ordered understanding of the goal.
        if self.enable_planning:
            await self._plan_phase()

        for round_no in range(1, self.max_rounds + 1):
            for agent in self.agents:
                won = await self._take_turn(agent)
                if won:
                    # If the storyboard has a sealed solution, pause for the
                    # human deduction phase before declaring victory.
                    has_solution = (
                        self.narrator is not None
                        and not self.narrator.storyboard.solution.is_empty()
                    )
                    if has_solution:
                        return await self._run_deduction_phase()
                    return await self._finalize(True, "escaped")
                if self.sim.state.finished:
                    return await self._finalize(
                        self.sim.state.won,
                        "escaped" if self.sim.state.won else "stalled",
                    )

        return await self._finalize(self.sim.state.won, "turn_limit")

    # ------------------------------------------------------------------ #
    # Collaborative planning (multi-agent debate before the game starts)
    # ------------------------------------------------------------------ #
    async def _plan_phase(self) -> None:
        """Have the team debate one shared, ordered escape plan up front.

        Pass 1: the first agent proposes a plan. Pass 2..N: each remaining agent
        CRITIQUES and refines it with their own private knowledge. The agreed plan
        is stored in cognition and shown on the OBJECTIVE BOARD every turn. Any LLM
        failure falls back to the running draft (or the setting's solution_path),
        so planning can never stall the game.
        """
        draft: list[str] = []
        for agent in self.agents:
            draft = await agent.propose_plan(self.sim.state, self.log, draft or None)

        if not draft:
            # Deterministic fallback so the board always has a plan to show.
            draft = list(self.setting.solution_path)

        if draft:
            self.cognition.set_plan(draft)
            # Plan is stored for agent cognition only — never streamed to readers.
            # Revealing the solution path at turn 0 spoils the mystery entirely.

    async def _take_turn(self, agent: GamePlayerAgent) -> bool:
        """Run one agent's ReAct turn. Returns True if the game was just won."""
        pid = agent.persona.id

        # Human players skip the LLM entirely and wait for UI input.
        if agent.persona.is_human:
            return await self._take_human_turn(agent)

        # Consume any pending nudge from the human observer: broadcast it as a
        # SYSTEM event (so it lands in MessageLog and agents see it) then use it
        # as this turn's critical_note override in place of stall detection.
        nudge = self._pending_nudge
        self._pending_nudge = None
        if nudge:
            await self._emit(
                EventKind.SYSTEM,
                None,
                f"[Human hint] {nudge}",
                turn=self.sim.state.turn,
            )

        reflect = self.cognition.should_reflect(self.sim.state.turn)
        critical_note = nudge or self.cognition.critical_guidance_for(pid, self.sim.state)
        if critical_note:
            # Record for agents to read; never stream to the UI — it's agent guidance.
            await self._emit(
                EventKind.SYSTEM,
                pid,
                f"CRITICAL: {critical_note}",
                turn=self.sim.state.turn,
                stream=False,
            )
            await self._narrate_event(
                SystemEventKind.CRITICAL_STUCK,
                actor_name=agent.persona.name,
                detail=critical_note,
            )

        world_key = world_fingerprint(self.sim.state)
        plan = None
        recommended = ""
        if self.enable_planner_tool:
            planning_brief = self.cognition.brief_for(
                pid,
                self.sim.state,
                reflect=reflect,
                blocked_note=None,
                critical_note=critical_note,
            )
            plan = self.planner.plan_turn(
                player_id=pid,
                state=self.sim.state,
                cognition=self.cognition,
                current_goal=planning_brief.current_goal,
                next_plan_step=planning_brief.next_plan_step,
            )
            # Surface the planner's single best move to the agent as an explicit
            # recommendation, so the 7B model defaults to executing it.
            if plan.best_progress_action is not None:
                recommended = describe_action_brief(plan.best_progress_action)
            elif plan.best_action is not None:
                recommended = describe_action_brief(plan.best_action)
            # Always stream planner advice so observers can compare it with the
            # eventual executed action in the same turn.
            await self._emit_planner_trace(
                pid,
                plan,
                self.sim.state.turn,
                chosen=plan.best_action,
                reason="advice",
            )

        # --- Loop guard: re-ask if the agent proposes a proven dead-end ---
        # External cognition decides whether a move is pointless (already tried in
        # this exact world state); we re-prompt with an explicit block note rather
        # than burning a turn on a guaranteed no-op.
        blocked_note: Optional[str] = None
        turn = None
        for attempt in range(self.max_redecide + 1):
            world_key = world_fingerprint(self.sim.state)
            brief = self.cognition.brief_for(
                pid,
                self.sim.state,
                reflect=reflect,
                blocked_note=blocked_note,
                critical_note=critical_note,
                recommended_action=recommended,
            )
            result = await agent.decide(
                self.sim.state,
                self.log,
                brief,
                prompt_sink=lambda prompt_text: self._emit(
                    EventKind.PROMPT,
                    pid,
                    prompt_text,
                    turn=self.sim.state.turn,
                    record=False,
                ),
            )
            if not result.ok or result.value is None:
                reason = (result.error or "unknown parse/validation failure").strip()
                await self._emit(
                    EventKind.SYSTEM,
                    pid,
                    f"{pid} hesitated (could not form a valid action). reason: {reason}",
                    turn=self.sim.state.turn,
                    stream=False,
                )
                # Recovery: keep the game flowing with a deterministic no-risk
                # fallback so one malformed model turn does not dead-end play.
                fallback = GameAction(action=GameActionType.LOOK)
                obs = self.sim.step(pid, fallback)
                obs_text = obs.message + (f" Hint: {obs.hint}" if obs.hint else "")
                await self._emit(
                    EventKind.OBSERVATION,
                    pid,
                    obs_text,
                    turn=self.sim.state.turn,
                    public=obs.public,
                    audience_id=None if obs.public else pid,
                )
                update = self.cognition.observe(pid, fallback, world_key, obs, self.sim.state)
                await self._announce_cognition(update)
                return obs.game_won
            candidate = result.value

            # Stall escalation: if the team is stuck and the agent emits a pure
            # free action, force a progress-biased planner fallback.
            if (
                self.enable_planner_tool
                and
                self.cognition.stuck
                and plan is not None
                and candidate.action.action in _FREE_ACTIONS
                and plan.best_progress_action is not None
            ):
                blocked_note = (
                    "STALL MODE: free/no-progress action rejected while stuck. "
                    "Choose a progress candidate (unlock/take/move/use/enter_code) now."
                )
                if attempt < self.max_redecide:
                    await self._emit(
                        EventKind.SYSTEM,
                        pid,
                        f"(stall gate) {blocked_note}",
                        turn=self.sim.state.turn,
                        stream=False,
                    )
                    continue
                turn = candidate.model_copy(deep=True)
                turn.action = plan.best_progress_action
                await self._emit_planner_trace(
                    pid,
                    plan,
                    self.sim.state.turn,
                    chosen=turn.action,
                    reason="stall_free_action",
                )
                await self._emit(
                    EventKind.SYSTEM,
                    pid,
                    (
                        "(planner override) stalled too long; "
                        f"executing {action_signature(turn.action)}"
                    ),
                    turn=self.sim.state.turn,
                    stream=False,
                )
                break

            if (
                self.enforce_candidate_policy
                and (self.cognition.stuck or blocked_note is not None)
                and not self.cognition.is_policy_candidate(
                    pid,
                    self.sim.state,
                    brief.current_goal,
                    candidate.action,
                    brief.next_plan_step,
                )
            ):
                blocked_note = (
                    "OUT-OF-POLICY: choose one of the listed CANDIDATE ACTIONS "
                    "for this turn. Your previous action was not in the "
                    "deterministic valid/reachable policy set."
                )
                if attempt >= self.max_redecide:
                    if self.enable_planner_tool and plan is not None:
                        turn = candidate.model_copy(deep=True)
                        turn.action = plan.best_action
                        await self._emit_planner_trace(
                            pid,
                            plan,
                            self.sim.state.turn,
                            chosen=turn.action,
                            reason="out_of_policy",
                        )
                        await self._emit(
                            EventKind.SYSTEM,
                            pid,
                            (
                                "(planner override) action remained out-of-policy; "
                                f"executing {action_signature(turn.action)}"
                            ),
                            turn=self.sim.state.turn,
                            stream=False,
                        )
                    else:
                        turn = candidate
                    break
                await self._emit(
                    EventKind.SYSTEM,
                    pid,
                    f"(policy gate) {blocked_note}",
                    turn=self.sim.state.turn,
                    stream=False,
                )
                continue
            if (
                block := self.cognition.blocked_reason(
                    pid, candidate.action, world_key, self.sim.state
                )
            ):
                # Tell the agent (and observers) why, then let it choose again.
                blocked_note = block
                if attempt >= self.max_redecide:
                    if self.enable_planner_tool and plan is not None:
                        turn = candidate.model_copy(deep=True)
                        turn.action = plan.best_action
                        await self._emit_planner_trace(
                            pid,
                            plan,
                            self.sim.state.turn,
                            chosen=turn.action,
                            reason="blocked_repeat",
                        )
                        await self._emit(
                            EventKind.SYSTEM,
                            pid,
                            (
                                "(planner override) repeated blocked action; "
                                f"executing {action_signature(turn.action)}"
                            ),
                            turn=self.sim.state.turn,
                            stream=False,
                        )
                        await self._narrate_event(
                            SystemEventKind.PLANNER_OVERRIDE,
                            actor_name=agent.persona.name,
                            detail=f"forced to try {action_signature(turn.action)} after repeating a blocked action",
                        )
                    else:
                        turn = candidate
                    break
                await self._emit(
                    EventKind.SYSTEM,
                    pid,
                    f"(loop avoided) {blocked_note}",
                    turn=self.sim.state.turn,
                    stream=False,
                )
                await self._narrate_event(
                    SystemEventKind.LOOP_AVOIDED,
                    actor_name=agent.persona.name,
                    detail=blocked_note,
                )
                continue
            # Progress gate (general): the agent chose a LOW-VALUE action (walking
            # to an explored room, re-inspecting a decoy/scenic object, idle speech)
            # while the deterministic planner has a DOMINANT high-value action
            # available (go to the room where the puzzle is solved, or solve it).
            # Weak local models routinely ignore the surfaced recommendation and
            # linger like this; without enforcement they never finish a solved
            # world. We re-prompt once, then override on the final attempt.
            #
            # Genuine puzzle attempts (enter_code / use / set_fuse) are EXEMPT so a
            # wrong-but-sincere try still executes, fails, and is recorded in
            # episodic memory (the team learns from it) instead of being swallowed.
            if (
                self.enable_planner_tool
                and plan is not None
                and plan.best_progress_action is not None
                and candidate.action.action not in _PROGRESS_GATE_EXEMPT
                and action_signature(plan.best_progress_action)
                != action_signature(candidate.action)
            ):
                progress_action = plan.best_progress_action
                progress_score = plan.score_of(progress_action)
                if progress_score is None:
                    progress_score = plan.top_score
                chosen_value = self.planner.immediate_value(
                    player_id=pid,
                    state=self.sim.state,
                    action=candidate.action,
                )
                dominant = (
                    progress_score >= _PROGRESS_GATE_DOMINANT_MIN
                    and progress_score - chosen_value >= _PROGRESS_GATE_GAP
                )
                if dominant and chosen_value <= _PROGRESS_GATE_LOW_VALUE:
                    blocked_note = (
                        "HIGHER-VALUE ACTION AVAILABLE: that move makes no real "
                        "progress. The team planner strongly recommends "
                        f"{describe_action_brief(plan.best_progress_action)}. "
                        "Do exactly that now, unless you can state a concrete "
                        "reason it is wrong."
                    )
                    if attempt < self.max_redecide:
                        await self._emit(
                            EventKind.SYSTEM,
                            pid,
                            f"(progress gate) {blocked_note}",
                            turn=self.sim.state.turn,
                            stream=False,
                        )
                        continue
                    turn = candidate.model_copy(deep=True)
                    turn.action = plan.best_progress_action
                    await self._emit_planner_trace(
                        pid,
                        plan,
                        self.sim.state.turn,
                        chosen=turn.action,
                        reason="progress_gate_override",
                    )
                    await self._emit(
                        EventKind.SYSTEM,
                        pid,
                        (
                            "(planner override) low-value action; "
                            f"executing {action_signature(turn.action)}"
                        ),
                        turn=self.sim.state.turn,
                        stream=False,
                    )
                    await self._narrate_event(
                        SystemEventKind.PLANNER_OVERRIDE,
                        actor_name=agent.persona.name,
                        detail=f"redirected from wandering to {action_signature(turn.action)}",
                    )
                    break
            turn = candidate
            break

        assert turn is not None

        # Debug visibility: stream the exact validated JSON turn chosen by the
        # player agent (kept out of shared agent memory to avoid feedback loops).
        if self.enable_planner_tool and plan is not None:
            await self._emit_planner_trace(
                pid,
                plan,
                self.sim.state.turn,
                chosen=turn.action,
                reason="executed",
            )
        await self._emit(
            EventKind.DECISION,
            pid,
            turn.model_dump_json(indent=2),
            turn=self.sim.state.turn,
            record=False,
        )

        # 1. Reflection checkpoint: update cognition silently — never shown as dialogue.
        if turn.reflection:
            self.cognition.set_reflection(turn.reflection.strip())

        # 2. Hypothesis: update blackboard silently — never shown as dialogue.
        if turn.hypothesis:
            self.cognition.record_hypothesis(pid, turn.hypothesis.strip())

        # 3. Player speak is disabled — speech is generated by the GM Narrator.
        # Intent (turn.intent) is passed to narrate_turn() below for richer dialogue.

        # 4. Apply the validated action to ground truth.
        world_key = world_fingerprint(self.sim.state)
        was_repeated_action = self.cognition.is_redundant(pid, turn.action, world_key)
        was_already_done = (
            self.cognition.already_done(pid, turn.action, self.sim.state) is not None
        )
        obs = self.sim.step(pid, turn.action)

        # 5. Record the grounded observation (+ any non-spoiler hint).
        # stream=False: agents read this through MessageLog; readers never see raw
        # engine text. The narrator translates it into story prose below.
        obs_text = obs.message + (f" Hint: {obs.hint}" if obs.hint else "")
        await self._emit(
            EventKind.OBSERVATION,
            pid,
            obs_text,
            turn=self.sim.state.turn,
            public=obs.public,
            audience_id=None if obs.public else pid,
            stream=False,
        )

        # 6. External cognition: episodic memory + progress / stall update.
        update = self.cognition.observe(pid, turn.action, world_key, obs, self.sim.state)
        await self._announce_cognition(update)

        # 6b. First-touch discovery beat for plot-critical items (inspect / take).
        if obs.success and turn.action.action in {GameActionType.INSPECT, GameActionType.TAKE}:
            await self._maybe_narrate_discovery(turn.action.target_id, agent.persona.name)

        # 6c. First visit to a new room: emit an atmospheric description card.
        if obs.success and turn.action.action == GameActionType.MOVE and self.narrator is not None:
            dest = turn.action.to_room or turn.action.target_id or ""
            await self._maybe_narrate_room_entry(dest, agent.persona.name)

        # 7. GM narration of this beat (observer-only; never fed back to agents).
        if self.narrator is not None:
            if was_repeated_action or was_already_done:
                execution_status = ActionExecutionStatus.REPEATED_ACTION_LOOP_CATCH
            elif obs.success:
                execution_status = ActionExecutionStatus.FRESH_ACTION_SUCCESS
            else:
                execution_status = ActionExecutionStatus.FRESH_ACTION_FAILURE
            board = derive_board(self.sim.state)
            actor_room = self.sim.state.player_locations.get(agent.persona.id, "")
            snapshot = WorldSnapshot(
                visible_objects=self.sim.state.visible_objects_for(agent.persona.id),
                teammate_locations=[
                    f"{p.name} in {self.sim.state.player_locations.get(p.id, '?')}"
                    for p in self.setting.players
                    if p.id != agent.persona.id
                ],
                solved_count=len(board.solved),
                total_puzzles=len(board.solved) + len(board.unsolved),
            )
            prose = await self.narrator.narrate_turn(
                scenario=self.setting.scenario,
                objective=self.setting.objective,
                turn=self.sim.state.turn,
                actor_name=agent.persona.name,
                actor_role=agent.persona.role,
                actor_skills=list(agent.persona.skills),
                actor_backstory=agent.persona.backstory,
                actor_gender=agent.persona.gender,
                action_text=describe_action(turn.action, agent.persona.name),
                speech=turn.intent,
                outcome=obs.message,
                success=obs.success,
                status=execution_status,
                room_id=actor_room,
                object_id=turn.action.target_id or "",
                world_snapshot=snapshot,
            )
            # Emit as SPEECH so iOS renders it as a chat bubble for this character.
            await self._emit(
                EventKind.SPEECH,
                pid,
                prose,
                turn=self.sim.state.turn,
                record=False,
                data={"action_execution_status": execution_status.value, "source": "narrator"},
            )

        return obs.game_won

    # ------------------------------------------------------------------ #
    # Human player turn
    # ------------------------------------------------------------------ #
    async def _take_human_turn(self, agent: GamePlayerAgent) -> bool:
        """Pause the game, emit candidate actions to the UI, await human input."""
        pid = agent.persona.id
        brief = self.cognition.brief_for(pid, self.sim.state)
        candidates = self.cognition.policy_candidates(
            pid, self.sim.state, brief.current_goal, brief.next_plan_step
        )
        candidate_list = [
            {
                "index": i,
                "description": describe_action_brief(c),
                "action": c.model_dump(exclude_none=True),
                "flavor": self._get_candidate_flavor(c),
            }
            for i, c in enumerate(candidates)
        ]
        await self._emit(
            EventKind.HUMAN_TURN,
            pid,
            f"Your turn, {agent.persona.name}. Choose an action.",
            turn=self.sim.state.turn,
            record=False,
            data={
                "player_id": pid,
                "player_name": agent.persona.name,
                "current_goal": brief.current_goal,
                "team_memory": brief.team_memory,
                "candidates": candidate_list,
            },
        )

        # Wait for the human to send an action (5-minute timeout → fallback LOOK).
        try:
            action_dict = await asyncio.wait_for(
                self._human_action_queue.get(), timeout=300.0
            )
            action = GameAction.model_validate(action_dict)
        except (asyncio.TimeoutError, Exception):
            action = GameAction(action=GameActionType.LOOK)

        world_key = world_fingerprint(self.sim.state)
        obs = self.sim.step(pid, action)
        obs_text = obs.message + (f" Hint: {obs.hint}" if obs.hint else "")
        # Human players see their own action result — they have no narrator.
        await self._emit(
            EventKind.OBSERVATION,
            pid,
            obs_text,
            turn=self.sim.state.turn,
            public=obs.public,
            audience_id=None if obs.public else pid,
        )
        update = self.cognition.observe(pid, action, world_key, obs, self.sim.state)
        await self._announce_cognition(update)
        # Discovery beat for plot-critical items the human touches.
        if obs.success and action.action in {GameActionType.INSPECT, GameActionType.TAKE}:
            await self._maybe_narrate_discovery(action.target_id, agent.persona.name)
        return obs.game_won

    # ------------------------------------------------------------------ #
    # Story-layer helpers (discovery beats + human-turn flavor)
    # ------------------------------------------------------------------ #
    async def _maybe_narrate_discovery(
        self, obj_id: str | None, actor_name: str
    ) -> None:
        """Emit a one-time narrator beat the first time a plot-critical item is touched.

        Fires on the first TAKE or successful INSPECT of any object that is either
        a required key for another object, or contains info that another object's
        code lock needs. Each object only fires once per game session.
        """
        if not obj_id or obj_id in self._narrated_discoveries or self.narrator is None:
            return
        obj = next((o for o in self.setting.objects if o.id == obj_id), None)
        if obj is None:
            return
        unlocks_desc = self._item_unlocks_desc(obj_id, obj)
        if not unlocks_desc:
            return  # not plot-critical; no story beat needed
        self._narrated_discoveries.add(obj_id)

        # If this is the proof object, flip narrator to revelation mode BEFORE the
        # discovery beat fires — the beat itself is the killer-reveal moment.
        sb = self.narrator.storyboard
        proof_obj = sb.mystery.proof_object_id or sb.solution.proof_object
        if proof_obj and obj_id == proof_obj:
            self.narrator.reveal_proof()
        prose = await self.narrator.narrate_discovery(
            actor_name=actor_name,
            item_id=obj_id,
            item_description=obj.description or "",
            unlocks_description=unlocks_desc,
            connection_lore=self.narrator.connection_lore_for(obj_id),
            scenario=self.setting.scenario,
        )
        if prose:
            await self._emit(
                EventKind.DISCOVERY,
                None,
                prose,
                turn=self.sim.state.turn,
                record=False,
                data={"object_id": obj_id, "is_proof": bool(proof_obj and obj_id == proof_obj)},
            )

    async def _maybe_narrate_room_entry(self, room_id: str, actor_name: str) -> None:
        """Emit a one-time atmospheric NARRATION card the first time a room is entered."""
        if not room_id or not self.narrator:
            return
        attr = f"_room_entered_{room_id}"
        if getattr(self, attr, False):
            return
        setattr(self, attr, True)
        prose = await self.narrator.narrate_room_entry(
            actor_name=actor_name,
            room_id=room_id,
            scenario=self.setting.scenario,
        )
        if prose:
            await self._emit(
                EventKind.NARRATION,
                None,
                prose,
                turn=self.sim.state.turn,
                record=False,
            )

    def _item_unlocks_desc(self, item_id: str, item_obj) -> str:
        """Return a non-empty string if this item is plot-critical.

        Returns the description of what it enables (for key/code objects)
        or the item's own description (for story-clue objects like the book
        that reveals the murderer's surname). An empty string means the item
        is not plot-critical and no discovery beat should fire.
        """
        # Case 1: physical key — another object requires this as a tool
        for obj in self.setting.objects:
            if obj.requires_tool == item_id:
                return obj.description or obj.id.replace("_", " ")
        if item_obj.contains_info:
            # Case 2: mechanical code source — a lock explicitly requires this code
            for obj in self.setting.objects:
                if obj.requires_code == item_obj.contains_info:
                    return obj.description or obj.id.replace("_", " ")
            # Case 3: story clue (e.g. murderer surname, victim note) — no lock
            # requires this mechanically, but it IS a narrative clue the human
            # reader needs to make the final deduction. Always fire a beat.
            return item_obj.description or item_id.replace("_", " ")
        return ""

    def _get_candidate_flavor(self, action) -> str:
        """Return the narrative description (or connection lore) for an action's target.

        Used to populate flavor text in the human player's action chip panel so
        they understand WHY a candidate action might matter before choosing it.
        """
        target_id = getattr(action, "target_id", None) or getattr(action, "item_id", None)
        if not target_id:
            return ""
        obj = next((o for o in self.setting.objects if o.id == target_id), None)
        if obj is None:
            return ""
        # Prefer connection_lore (story-why) over bare description when available.
        if self.narrator:
            lore = self.narrator.connection_lore_for(target_id)
            if lore:
                return lore
        return obj.description or ""

    # ------------------------------------------------------------------ #
    # External cognition announcements (progress markers / stall signals)
    # ------------------------------------------------------------------ #
    async def _announce_cognition(self, update) -> None:
        """Surface intermediate rewards and stall signals to the team + story."""
        if update.new_milestones:
            # Show system pills only for meaningful game events, not room arrivals.
            # Room-arrival milestones start with "reached " — those are covered by
            # the room-entry narration beat and don't need a separate pill.
            story_milestones = [
                m for m in update.new_milestones
                if not m.startswith("reached ")
            ]
            if story_milestones:
                markers = ", ".join(
                    m.replace(":", " ").replace("_", " ") for m in story_milestones
                )
                await self._emit(
                    EventKind.SYSTEM,
                    None,
                    f"✓ {markers}.",
                    turn=self.sim.state.turn,
                )
            # Milestone narrator beats are intentionally removed: the turn narrator
            # and discovery beats already cover every action. A third beat per turn
            # produces redundant generic prose and dilutes the story rhythm.
        if update.became_stuck:
            await self._emit(
                EventKind.SYSTEM,
                None,
                "The team seems stuck — re-evaluate the clues, challenge your "
                "assumptions, and try a DIFFERENT plan.",
                turn=self.sim.state.turn,
            )
            # No narrator beat here — the MOVE action that follows will fire a
            # room-entry beat, and two consecutive NARRATOR blocks without player
            # dialogue between them reads as a visual stutter.

    async def _narrate_event(
        self,
        event_kind: SystemEventKind,
        *,
        actor_name: str,
        detail: str,
    ) -> None:
        """Emit a NARRATION beat for an internal system event if narrator is active."""
        if self.narrator is None:
            return
        prose = await self.narrator.narrate_system_event(
            event_kind=event_kind,
            actor_name=actor_name,
            detail=detail,
            scenario=self.setting.scenario,
        )
        if prose:
            await self._emit(
                EventKind.NARRATION,
                None,
                prose,
                turn=self.sim.state.turn,
                record=False,
            )

    async def _emit_planner_trace(
        self,
        player_id: str,
        plan,
        turn: int,
        *,
        chosen: GameAction | None = None,
        reason: str | None = None,
    ) -> None:
        """Stream compact planner telemetry for observers/debuggers.

        This event is intentionally NOT recorded into `MessageLog` so planner
        diagnostics never bloat agent context.
        """
        candidates = []
        for idx, option in enumerate(plan.ranked, start=1):
            candidates.append(
                {
                    "rank": idx,
                    "action": action_signature(option.action),
                    "score": round(option.score, 3),
                    "rationale": option.rationale,
                }
            )
        chosen_text = action_signature(chosen or plan.best_action)
        top = [f"{c['rank']}) {c['action']} score={c['score']:.1f}" for c in candidates]
        reason_text = f" reason={reason};" if reason else ""
        text = (
            f"planner:{reason_text} chosen={chosen_text}; "
            f"top={'; '.join(top)}"
        )
        payload = {
            "reason": reason,
            "chosen": chosen_text,
            "candidates": candidates,
        }
        await self._emit(
            EventKind.PLANNER,
            player_id,
            text,
            turn=turn,
            record=False,
            data=payload,
        )

    async def _emit(
        self,
        kind: EventKind,
        actor_id: Optional[str],
        text: str,
        *,
        turn: int,
        public: bool = True,
        audience_id: Optional[str] = None,
        record: bool = True,
        stream: bool = True,
        data: dict | None = None,
    ) -> None:
        event = Event(
            kind=kind,
            actor_id=actor_id,
            text=text,
            turn=turn,
            public=public,
            audience_id=audience_id,
            data=data,
        )
        # NARRATION is a presentation overlay: stream it to observers but keep it
        # OUT of the shared log so it can never feed back into player agents.
        if record:
            self.log.add(event)
        # stream=False: write to agent MessageLog for context, but never send to
        # the WebSocket — internal planner/override messages are not user-facing.
        if stream and self.on_event is not None:
            await self.on_event(event)

    async def _run_deduction_phase(self) -> GameResult:
        """Pause the game and ask the human to name the answer. Up to 3 attempts.

        The AI agents have mechanically solved the puzzle. Now the human must
        make the final call — identify the killer, the sabotaged system, the
        stolen object, etc. — based on the evidence gathered during the game.
        """
        solution = self.narrator.storyboard.solution  # type: ignore[union-attr]
        max_attempts = 3

        for attempt in range(max_attempts):
            hint = ""
            if attempt == 1:
                hint = solution.hint_1
            elif attempt >= 2:
                hint = solution.hint_2

            await self._emit(
                EventKind.HUMAN_DEDUCTION,
                None,
                solution.question,
                turn=self.sim.state.turn,
                record=False,
                data={
                    "question": solution.question,
                    "attempt": attempt + 1,
                    "max_attempts": max_attempts,
                    "hint": hint,
                },
            )

            try:
                answer = await asyncio.wait_for(
                    self._deduction_queue.get(), timeout=300.0
                )
            except asyncio.TimeoutError:
                answer = ""

            if solution.matches(answer):
                return await self._finalize(True, "escaped")

            if attempt < max_attempts - 1:
                next_hint = solution.hint_1 if attempt == 0 else solution.hint_2
                msg = "Not quite." + (f" {next_hint}" if next_hint else "")
                await self._emit(
                    EventKind.SYSTEM,
                    None,
                    msg,
                    turn=self.sim.state.turn,
                )

        return await self._finalize(False, "wrong_deduction")

    async def _finalize(self, won: bool, reason: str) -> GameResult:
        """Emit closing narration (if any) and build the final result."""
        if self.narrator is not None:
            wrong_deduction = reason == "wrong_deduction"
            ending = await self.narrator.narrate_ending(
                self.setting, won, wrong_deduction=wrong_deduction
            )
            await self._emit(
                EventKind.NARRATION, None, ending, turn=self.sim.state.turn, record=False
            )
        return self._result(won, reason)

    def _result(self, won: bool, reason: str) -> GameResult:
        return GameResult(
            won=won,
            turns=self.sim.state.turn,
            reason=reason,
            events=list(self.log.events),
        )
