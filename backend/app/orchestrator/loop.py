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

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from app.agents.game_player_agent import GamePlayerAgent
from app.agents.gm_narrator import GameMasterNarrator
from app.agents.gm_narrator_prompt import ActionExecutionStatus, describe_action
from app.cognition.team_cognition import (
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
# Minimum planner-score advantage of the best action over a chosen MOVE before
# the movement progress gate intervenes. Large so only clear wandering (walking
# away from a high-value next step) is corrected, not normal forward exploration.
_MOVE_GATE_GAP = 30.0


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
            steps = "; ".join(f"{i + 1}) {s}" for i, s in enumerate(draft))
            await self._emit(
                EventKind.SYSTEM,
                None,
                f"📋 Shared plan agreed by the team: {steps}",
                turn=0,
            )

    async def _take_turn(self, agent: GamePlayerAgent) -> bool:
        """Run one agent's ReAct turn. Returns True if the game was just won."""
        pid = agent.persona.id
        reflect = self.cognition.should_reflect(self.sim.state.turn)
        critical_note = self.cognition.critical_guidance_for(pid, self.sim.state)
        if critical_note:
            await self._emit(
                EventKind.SYSTEM,
                pid,
                f"CRITICAL: {critical_note}",
                turn=self.sim.state.turn,
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
                        )
                    else:
                        turn = candidate
                    break
                await self._emit(
                    EventKind.SYSTEM,
                    pid,
                    f"(policy gate) {blocked_note}",
                    turn=self.sim.state.turn,
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
                        )
                    else:
                        turn = candidate
                    break
                await self._emit(
                    EventKind.SYSTEM,
                    pid,
                    f"(loop avoided) {blocked_note}",
                    turn=self.sim.state.turn,
                )
                continue
            # Movement progress gate (narrow): the agent chose to walk to a room
            # while the deterministic planner has a much higher-value action
            # available (typically: go to the room where the next puzzle can be
            # solved, or solve it). Weak local models wander between explored
            # rooms here. We re-prompt once, then override the wandering move on
            # the final attempt. Scoped to MOVE only so it never interferes with
            # the agent's puzzle-action choices (take/use/enter_code/inspect).
            if (
                self.enable_planner_tool
                and plan is not None
                and candidate.action.action == GameActionType.MOVE
                and plan.best_progress_action is not None
                and action_signature(plan.best_progress_action)
                != action_signature(candidate.action)
            ):
                chosen_score = plan.score_of(candidate.action)
                chosen_value = chosen_score if chosen_score is not None else 0.0
                if plan.top_score - chosen_value >= _MOVE_GATE_GAP:
                    blocked_note = (
                        "BETTER MOVE AVAILABLE: the team planner has a much "
                        "higher-value action — "
                        f"{describe_action_brief(plan.best_progress_action)}. "
                        "Do that instead of wandering, unless you can state a "
                        "concrete reason it is wrong."
                    )
                    if attempt < self.max_redecide:
                        await self._emit(
                            EventKind.SYSTEM,
                            pid,
                            f"(move gate) {blocked_note}",
                            turn=self.sim.state.turn,
                        )
                        continue
                    turn = candidate.model_copy(deep=True)
                    turn.action = plan.best_progress_action
                    await self._emit_planner_trace(
                        pid,
                        plan,
                        self.sim.state.turn,
                        chosen=turn.action,
                        reason="move_gate_override",
                    )
                    await self._emit(
                        EventKind.SYSTEM,
                        pid,
                        (
                            "(planner override) low-value wander; "
                            f"executing {action_signature(turn.action)}"
                        ),
                        turn=self.sim.state.turn,
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

        # 1. Reflection checkpoint output becomes the team's summarized memory.
        if turn.reflection:
            self.cognition.set_reflection(turn.reflection.strip())
            await self._emit(
                EventKind.SPEECH,
                pid,
                f"(reflection) {turn.reflection.strip()}",
                turn=self.sim.state.turn,
            )

        # 2. Share the proposed plan with the team (blackboard + public speech).
        if turn.hypothesis:
            self.cognition.record_hypothesis(pid, turn.hypothesis.strip())
            await self._emit(
                EventKind.SPEECH,
                pid,
                f"(idea) {turn.hypothesis.strip()}",
                turn=self.sim.state.turn,
            )

        # 3. Public speech (critique / support / observation), if any.
        if turn.speak:
            await self._emit(EventKind.SPEECH, pid, turn.speak, turn=self.sim.state.turn)

        # 4. Apply the validated action to ground truth.
        world_key = world_fingerprint(self.sim.state)
        was_repeated_action = self.cognition.is_redundant(pid, turn.action, world_key)
        was_already_done = (
            self.cognition.already_done(pid, turn.action, self.sim.state) is not None
        )
        obs = self.sim.step(pid, turn.action)

        # 5. Record the grounded observation (+ any non-spoiler hint).
        obs_text = obs.message + (f" Hint: {obs.hint}" if obs.hint else "")
        await self._emit(
            EventKind.OBSERVATION,
            pid,
            obs_text,
            turn=self.sim.state.turn,
            public=obs.public,
            audience_id=None if obs.public else pid,
        )

        # 6. External cognition: episodic memory + progress / stall update.
        update = self.cognition.observe(pid, turn.action, world_key, obs, self.sim.state)
        await self._announce_cognition(update)

        # 7. GM narration of this beat (observer-only; never fed back to agents).
        if self.narrator is not None:
            if was_repeated_action or was_already_done:
                execution_status = ActionExecutionStatus.REPEATED_ACTION_LOOP_CATCH
            elif obs.success:
                execution_status = ActionExecutionStatus.FRESH_ACTION_SUCCESS
            else:
                execution_status = ActionExecutionStatus.FRESH_ACTION_FAILURE
            prose = await self.narrator.narrate_turn(
                scenario=self.setting.scenario,
                objective=self.setting.objective,
                turn=self.sim.state.turn,
                actor_name=agent.persona.name,
                actor_role=agent.persona.role,
                actor_skills=list(agent.persona.skills),
                actor_backstory=agent.persona.backstory,
                action_text=describe_action(turn.action, agent.persona.name),
                speech=turn.speak,
                outcome=obs.message,
                success=obs.success,
                status=execution_status,
            )
            await self._emit(
                EventKind.NARRATION,
                pid,
                prose,
                turn=self.sim.state.turn,
                record=False,
                data={"action_execution_status": execution_status.value},
            )

        return obs.game_won

    # ------------------------------------------------------------------ #
    # External cognition announcements (progress markers / stall signals)
    # ------------------------------------------------------------------ #
    async def _announce_cognition(self, update) -> None:
        """Surface intermediate rewards and stall signals to the team + story."""
        if update.new_milestones:
            markers = ", ".join(m.replace(":", " ").replace("_", " ") for m in update.new_milestones)
            await self._emit(
                EventKind.SYSTEM,
                None,
                f"✓ Progress! The team just achieved: {markers}.",
                turn=self.sim.state.turn,
            )
        if update.became_stuck:
            await self._emit(
                EventKind.SYSTEM,
                None,
                "The team seems stuck — re-evaluate the clues, challenge your "
                "assumptions, and try a DIFFERENT plan.",
                turn=self.sim.state.turn,
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
        if self.on_event is not None:
            await self.on_event(event)

    async def _finalize(self, won: bool, reason: str) -> GameResult:
        """Emit closing narration (if any) and build the final result."""
        if self.narrator is not None:
            ending = await self.narrator.narrate_ending(self.setting, won)
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
