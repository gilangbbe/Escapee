"""TeamCognition — the deterministic "external brain" shared by all agents.

It tracks what the LLM cannot be trusted to hold over dozens of turns: a
symbolic snapshot of the world, an episodic memory of every attempt, which
moves are now provably pointless (loop detection), how much real progress has
been made (milestones / intermediate rewards), what remains unexplored
(curiosity), and when the team has stalled and should reflect.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.context.game_builder import TeamBrief
from app.engine.game_actions import GameAction, GameActionType
from app.engine.game_state import GameState
from app.engine.observation import Observation
from app.schemas.game_setting import ObjectState

# Action kinds that are "free": pure talk / orientation. We never block these as
# redundant, because communication and re-reading the room are cheap and useful.
_FREE_ACTIONS = {GameActionType.SAY, GameActionType.LOOK}

# Object states that count as "opened / solved" for milestone tracking.
_OPENED_STATES = {ObjectState.OPEN, ObjectState.UNLOCKED, ObjectState.POWERED}

# Object states that mean "still a locked puzzle to solve".
_LOCKED_STATES = {ObjectState.LOCKED, ObjectState.LOCKED_BOLT, ObjectState.LOCKED_ROOM}


def _state_satisfies(expected: ObjectState, current: ObjectState | None) -> bool:
    """Return True when current state satisfies the expected target state."""
    if current is None:
        return False
    if current == expected:
        return True
    equivalent_open_states = {ObjectState.OPEN, ObjectState.UNLOCKED}
    return expected in equivalent_open_states and current in equivalent_open_states


@dataclass(frozen=True)
class CandidateAction:
    """Deterministic next-action option exposed to the LLM policy layer."""

    action: GameAction
    tag: str  # PROGRESS | EXPLORATION | COORDINATION


def action_signature(action: GameAction) -> str:
    """Compact, stable label for an action (ignores free-text message)."""
    parts = [action.action.value]
    for fld in ("target_id", "item_id", "code", "fuse", "position", "to_room", "to_player_id"):
        value = getattr(action, fld, None)
        if value:
            parts.append(f"{fld}={value}")
    return " ".join(parts)


def describe_action_brief(action: GameAction) -> str:
    """One-line imperative description of an action (for RECOMMENDED ACTION)."""
    a = action.action
    if a == GameActionType.MOVE and action.to_room:
        return f"move to {action.to_room}"
    if a == GameActionType.INSPECT and action.target_id:
        return f"inspect {action.target_id}"
    if a == GameActionType.TAKE and action.target_id:
        return f"take {action.target_id}"
    if a == GameActionType.ENTER_CODE and action.target_id and action.code:
        return f"enter_code on {action.target_id} with code {action.code}"
    if a == GameActionType.USE and action.item_id and action.target_id:
        return f"use {action.item_id} on {action.target_id}"
    if a == GameActionType.SET_FUSE and action.target_id and action.fuse and action.position:
        return f"set_fuse {action.fuse} to {action.position} on {action.target_id}"
    if a == GameActionType.GIVE and action.item_id and action.to_player_id:
        return f"give {action.item_id} to {action.to_player_id}"
    if a == GameActionType.SAY:
        return "say one short clue/blocker update"
    if a == GameActionType.LOOK:
        return "look around"
    return action_signature(action)


def world_fingerprint(state: GameState) -> str:
    """A short, stable hash of the symbolic world state.

    Two states with the same fingerprint are interaction-equivalent: any given
    (player, action) yields the identical outcome in both. This is what lets us
    say "nothing has changed, so don't retry that."
    """
    parts: list[str] = []
    for oid in sorted(state.object_state):
        parts.append(f"s:{oid}={state.object_state[oid].value}")
    for oid in sorted(state.object_location):
        parts.append(f"l:{oid}={state.object_location[oid]}")
    for pid in sorted(state.player_locations):
        parts.append(f"p:{pid}={state.player_locations[pid]}")
    for pid in sorted(state.player_inventories):
        inv = ",".join(sorted(state.player_inventories[pid]))
        parts.append(f"i:{pid}=[{inv}]")
    parts.append("rooms:" + ",".join(sorted(state.accessible_rooms)))
    parts.append("power:" + ",".join(sorted(state.power_flags)))
    parts.append("info:" + ",".join(sorted(state.discovered_info)))
    blob = "|".join(parts)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def compute_milestones(state: GameState) -> set[str]:
    """Derive the set of achieved milestones from authoritative state."""
    out: set[str] = set()
    start = state.setting.effective_start_room()
    for obj in state.setting.objects:
        st = state.object_state.get(obj.id)
        if st in _OPENED_STATES and obj.state not in _OPENED_STATES:
            out.add(f"opened:{obj.id}")
        if st == ObjectState.TAKEN:
            out.add(f"took:{obj.id}")
    for room in state.accessible_rooms:
        if room != start:
            out.add(f"reached:{room}")
    for info in state.discovered_info:
        out.add(f"learned:{info}")
    for flag in state.power_flags:
        out.add(f"power:{flag}")
    return out


def _humanize(token: str) -> str:
    """'opened:supply_locker' -> 'opened supply locker'."""
    kind, _, rest = token.partition(":")
    return f"{kind} {rest.replace('_', ' ')}".strip()


def _requirement_hint(obj) -> str:
    """A NON-spoiler note about what a locked object still needs (no codes leaked)."""
    if obj.requires_code:
        return " (needs a code)"
    if obj.requires_tool:
        return " (needs the right tool used on it)"
    if obj.requires_liquid:
        return " (needs a specific liquid)"
    if obj.requires_power:
        return " (needs power restored first)"
    if obj.fuses is not None:
        return " (a power source — set its fuses)"
    return ""


def _has_requirement(obj) -> bool:
    """True when an object has at least one defined unlock/activation mechanism."""
    return bool(
        obj.requires_code
        or obj.requires_tool
        or obj.requires_liquid
        or obj.requires_power
        or obj.fuses is not None
    )


def solved_object_ids(state: GameState) -> set[str]:
    """Ids of objects that are already opened/unlocked/powered or taken."""
    out: set[str] = set()
    for obj in state.setting.objects:
        cur = state.object_state.get(obj.id)
        if cur == ObjectState.TAKEN:
            out.add(obj.id)
        elif cur in _OPENED_STATES and obj.state not in _OPENED_STATES:
            out.add(obj.id)
    return out


@dataclass
class SolutionBoard:
    """A deterministic, always-correct snapshot of progress derived from state.

    This is the antidote to memory drift: instead of trusting the model to recall
    what it already accomplished, we recompute SOLVED vs STILL-TO-DO from the
    authoritative `GameState` every turn.
    """

    objective: str = ""
    solved: list[str] = field(default_factory=list)        # facts that are DONE
    unsolved: list[str] = field(default_factory=list)      # open puzzles / the goal

    def render(self) -> str:
        solved = "; ".join(self.solved) or "(nothing yet)"
        todo = "; ".join(self.unsolved) or "(none — you may be ready to win)"
        return (
            f"GOAL: {self.objective}\n"
            f"✅ SOLVED (already done — NEVER redo these): {solved}\n"
            f"🔲 STILL TO DO (focus here): {todo}"
        )


def derive_board(state: GameState) -> SolutionBoard:
    """Compute the SOLVED / STILL-TO-DO board straight from authoritative state."""
    setting = state.setting
    start = setting.effective_start_room()
    solved: list[str] = []
    unsolved: list[str] = []

    for obj in setting.objects:
        cur = state.object_state.get(obj.id)
        if cur in _OPENED_STATES and obj.state not in _OPENED_STATES:
            solved.append(f"{obj.id} is {cur.value}")
        elif cur == ObjectState.TAKEN:
            solved.append(f"{obj.id} has been taken")
    for info in sorted(state.discovered_info):
        solved.append(f"learned [{info}]")
    for flag in sorted(state.power_flags):
        solved.append(f"power on [{flag}]")
    for room in sorted(state.accessible_rooms):
        if room != start:
            solved.append(f"reached {room}")

    for obj in setting.objects:
        cur = state.object_state.get(obj.id)
        if cur in _LOCKED_STATES and obj.interactable:
            unsolved.append(f"{obj.id} is still {cur.value}{_requirement_hint(obj)}")

    win = setting.win_condition
    cur_win = state.object_state.get(win.object_id)
    if not _state_satisfies(win.state, cur_win):
        now = cur_win.value if cur_win else "unknown"
        unsolved.append(
            f"WIN CONDITION: [{win.object_id}] must reach '{win.state.value}' (now '{now}')"
        )

    return SolutionBoard(
        objective=setting.objective or "Escape the room together.",
        solved=solved,
        unsolved=unsolved,
    )


def derive_current_goal(board: SolutionBoard) -> str:
    """Pick one actionable goal for this turn from unsolved objectives."""
    for item in board.unsolved:
        if "WIN CONDITION" not in item:
            return item
    if board.unsolved:
        return board.unsolved[0]
    return "Satisfy the win condition now."


def _goal_object_id(current_goal: str) -> str | None:
    """Extract leading object id from goal lines of various formats."""
    if " is still " in current_goal:
        return current_goal.split(" is still ", 1)[0].strip()
    if "WIN CONDITION:" in current_goal and "[" in current_goal and "]" in current_goal:
        try:
            return current_goal.split("[", 1)[1].split("]", 1)[0].strip()
        except Exception:
            return None
    # "set fuse X to ON on <object_id> ..."  → extract object_id
    if current_goal.startswith("set fuse ") and " on " in current_goal:
        after_on = current_goal.split(" on ", 1)[1]
        return after_on.split()[0].strip()
    # "take <object_id> from ..."  → extract object_id
    if current_goal.startswith("take ") and " from " in current_goal:
        return current_goal.split("take ", 1)[1].split(" from ")[0].strip()
    return None


@dataclass
class PlanStep:
    text: str
    done: bool = False


@dataclass
class TeamPlan:
    """The shared, debated step-by-step escape plan (guidance for the team).

    Steps are free text agreed by the agents during the planning phase. We
    auto-tick a step once an object it names becomes solved, so the displayed
    plan tracks real progress without trusting the model's memory.
    """

    steps: list[PlanStep] = field(default_factory=list)

    def refresh(self, milestones: set[str]) -> None:
        """Tick plan steps using milestone-type-aware matching.

        Milestones look like "took:X", "opened:X", "reached:room_Y", "power:X".
        We only tick a step when the milestone *kind* matches the verb in the step
        text, so a "took:circuit_breaker_tool" milestone does NOT prematurely tick
        "Use circuit_breaker_tool on ..." (only a take/pick-up step would match).
        """
        _TAKE_VERBS = ("take", "pick up", "grab", "retrieve", "collect", "get")
        _OPEN_VERBS = ("enter", "unlock", "open", "use", "code", "set")
        _MOVE_VERBS = ("move", "reach", "enter", "pass", "go", "advance")
        _POWER_VERBS = ("power", "fuse", "disable", "enable", "online", "activate")
        _LEARN_VERBS = ("inspect", "read", "find", "discover", "note", "clue")

        for step in self.steps:
            if step.done:
                continue
            low = step.text.lower().replace("_", " ")
            for milestone in milestones:
                kind, _, rest = milestone.partition(":")
                token = rest.replace("_", " ").strip()
                if not token:
                    continue
                matched = False
                if kind == "took":
                    # Only tick steps whose primary verb is a take-like action.
                    if token in low and any(kw in low for kw in _TAKE_VERBS):
                        matched = True
                elif kind == "opened":
                    # Tick steps that interact with this object to open/unlock it.
                    if token in low and any(kw in low for kw in _OPEN_VERBS):
                        matched = True
                elif kind == "reached":
                    # Tick movement steps ("reached:room_2" → token "room 2").
                    if token in low and any(kw in low for kw in _MOVE_VERBS):
                        matched = True
                elif kind == "power":
                    # Tick power/fuse-related steps.
                    if any(kw in low for kw in _POWER_VERBS):
                        matched = True
                elif kind == "learned":
                    if token in low and any(kw in low for kw in _LEARN_VERBS):
                        matched = True
                if matched:
                    step.done = True
                    break

    def render(self) -> str:
        if not self.steps:
            return "(no agreed plan yet — propose one)"
        return "\n".join(
            f"  {i + 1}. [{'x' if s.done else ' '}] {s.text}"
            for i, s in enumerate(self.steps)
        )


@dataclass
class AttemptRecord:
    """One executed action, with the world it was taken in and its outcome."""

    turn: int
    player_id: str
    signature: str
    world_key: str       # fingerprint BEFORE the action
    success: bool
    outcome: str


@dataclass
class CognitionConfig:
    stall_threshold: int = 6      # no-progress turns before "stuck"
    reflect_every: int = 6        # turns between reflection checkpoints
    curiosity_limit: int = 4      # max unexplored targets to surface
    failed_limit: int = 6         # max blocked/failed attempts to show
    hypothesis_limit: int = 4     # max teammate proposals to show


@dataclass
class ProgressUpdate:
    """What changed cognitively after an action was executed."""

    new_milestones: list[str] = field(default_factory=list)
    became_stuck: bool = False
    became_unstuck: bool = False


@dataclass
class TeamCognition:
    """Deterministic shared memory + loop/progress/stall reasoning for the team."""

    config: CognitionConfig = field(default_factory=CognitionConfig)

    attempts: list[AttemptRecord] = field(default_factory=list)
    hypotheses: dict[str, str] = field(default_factory=dict)
    team_summary: str = ""                       # latest reflection (summarized memory)
    milestones: set[str] = field(default_factory=set)
    plan: TeamPlan = field(default_factory=TeamPlan)  # the shared, debated escape plan

    _seen: set[tuple[str, str, str]] = field(default_factory=set, init=False)  # (player,sig,world)
    _tried_targets: set[str] = field(default_factory=set, init=False)
    _inspected_targets: set[str] = field(default_factory=set, init=False)  # objects already examined
    _turns_since_progress: int = field(default=0, init=False)
    stuck: bool = field(default=False, init=False)
    _last_critical_turn: dict[str, int] = field(default_factory=dict, init=False)

    # ------------------------------------------------------------------ #
    # Blackboard
    # ------------------------------------------------------------------ #
    def record_hypothesis(self, player_id: str, text: str) -> None:
        text = (text or "").strip()
        if text:
            self.hypotheses[player_id] = text

    def set_reflection(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self.team_summary = text

    def set_plan(self, steps: list[str]) -> None:
        """Install the shared step-by-step plan agreed during the planning phase."""
        clean = [s.strip() for s in steps if s and s.strip()]
        self.plan = TeamPlan(steps=[PlanStep(text=s) for s in clean])

    # ------------------------------------------------------------------ #
    # Loop detection (the no-repeat guard)
    # ------------------------------------------------------------------ #
    def already_done(self, player_id: str, action: GameAction, state: GameState) -> str | None:
        """Goal-level redundancy: is this action's objective ALREADY satisfied?

        Unlike `is_redundant` (which needs the exact same world fingerprint), this
        reads the CURRENT authoritative state, so it catches the classic failure
        mode: re-opening a locker, re-taking an item, or re-entering a room that
        was already handled many turns ago — even though the wider world changed.
        """
        a = action.action
        tid = getattr(action, "target_id", None)

        if a == GameActionType.TAKE and tid:
            if state.object_state.get(tid) == ObjectState.TAKEN:
                return f"'{tid}' has already been taken — it is in someone's inventory."
        if a in (GameActionType.ENTER_CODE, GameActionType.SET_FUSE) and tid:
            if state.object_state.get(tid) in _OPENED_STATES:
                return f"'{tid}' is already open/unlocked — codes and fuses won't do anything more."
        if a == GameActionType.USE and tid:
            obj = state.obj(tid)
            if state.object_state.get(tid) in _OPENED_STATES and obj is not None and (
                obj.requires_tool or obj.requires_liquid or obj.requires_power
            ):
                return f"'{tid}' is already open — using an item on it again does nothing."
        if a == GameActionType.MOVE:
            to_room = getattr(action, "to_room", None)
            if to_room and state.player_locations.get(player_id) == to_room:
                return f"you are already in '{to_room}'."
        if a == GameActionType.INSPECT and tid:
            if tid in self._inspected_targets:
                return (
                    f"'{tid}' was already inspected — inspecting is idempotent, so "
                    f"examining it again reveals nothing new."
                )
        return None

    def blocked_reason(
        self, player_id: str, action: GameAction, world_key: str, state: GameState
    ) -> str | None:
        """The single gate the orchestrator consults before executing an action.

        Returns a human note if the move should be re-decided, else None. Free
        actions (say/look) always pass.
        """
        if action.action in _FREE_ACTIONS:
            return None
        done = self.already_done(player_id, action, state)
        if done:
            return (
                f"ALREADY DONE — {done} This objective is complete; pick the NEXT "
                f"unsolved step toward the goal instead of repeating it."
            )
        if self.is_redundant(player_id, action, world_key):
            return self.redundancy_note(action)
        return None

    def is_redundant(self, player_id: str, action: GameAction, world_key: str) -> bool:
        """True if this player already tried this exact action in this exact world.

        Free actions (say/look) are never redundant. Everything else is a proven
        no-op until the world fingerprint changes.
        """
        if action.action in _FREE_ACTIONS:
            return False
        return (player_id, action_signature(action), world_key) in self._seen

    def redundancy_note(self, action: GameAction) -> str:
        sig = action_signature(action)
        prior = next((a for a in reversed(self.attempts) if a.signature == sig), None)
        outcome = f" Last time: {prior.outcome}" if prior else ""
        return (
            f"BLOCKED: '{sig}' was already tried and the world has NOT changed since, "
            f"so the result is identical.{outcome} Choose a DIFFERENT action — explore "
            f"something untried, apply a clue, or coordinate with a teammate."
        )

    # ------------------------------------------------------------------ #
    # Recording outcomes + progress / stall tracking
    # ------------------------------------------------------------------ #
    def observe(
        self, player_id: str, action: GameAction, world_key: str, obs: Observation, state: GameState
    ) -> ProgressUpdate:
        sig = action_signature(action)
        self.attempts.append(
            AttemptRecord(
                turn=state.turn,
                player_id=player_id,
                signature=sig,
                world_key=world_key,
                success=obs.success,
                outcome=obs.message,
            )
        )
        self._seen.add((player_id, sig, world_key))
        for fld in ("target_id", "item_id"):
            tid = getattr(action, fld, None)
            if tid:
                self._tried_targets.add(tid)
        # Inspect is idempotent in this engine: a second inspect of the same object
        # never yields new information. Remember examined objects so we stop
        # re-proposing inspects of them (the dominant time-waster for 7B agents).
        if action.action == GameActionType.INSPECT and action.target_id:
            self._inspected_targets.add(action.target_id)

        update = ProgressUpdate()
        current = compute_milestones(state)
        new = current - self.milestones
        if new:
            update.new_milestones = sorted(new)
            self.milestones = current
            self._turns_since_progress = 0
            if self.stuck:
                self.stuck = False
                update.became_unstuck = True
        else:
            self._turns_since_progress += 1
            if not self.stuck and self._turns_since_progress >= self.config.stall_threshold:
                self.stuck = True
                update.became_stuck = True
        return update

    # ------------------------------------------------------------------ #
    # Reflection scheduling
    # ------------------------------------------------------------------ #
    def should_reflect(self, turn: int) -> bool:
        return turn > 0 and turn % self.config.reflect_every == 0

    # ------------------------------------------------------------------ #
    # Brief assembly (what the acting agent sees)
    # ------------------------------------------------------------------ #
    def _blocked_for(self, player_id: str, state: GameState) -> list[str]:
        """Actions that are now provably pointless for this player (no-repeat)."""
        world_key = world_fingerprint(state)
        out: list[str] = []
        seen_sigs: set[str] = set()
        for a in reversed(self.attempts):
            if a.signature in seen_sigs:
                continue
            # Only surface moves that would be redundant RIGHT NOW for this player.
            if (player_id, a.signature, world_key) in self._seen and not a.success:
                out.append(f"{a.signature} — {a.outcome}")
                seen_sigs.add(a.signature)
            if len(out) >= self.config.failed_limit:
                break
        return out

    def _unexplored(self, player_id: str, state: GameState) -> list[str]:
        """Reachable objects this team has never interacted with (curiosity)."""
        out: list[str] = []
        for oid in state.visible_objects_for(player_id):
            if oid not in self._tried_targets:
                out.append(oid)
            if len(out) >= self.config.curiosity_limit:
                break
        return out

    def _ownership(self, state: GameState) -> list[str]:
        """Assign unsolved object-focused tasks to players (round-robin)."""
        players = [p.id for p in state.setting.players]
        if not players:
            return []
        assigned: list[str] = []
        idx = 0
        for obj in state.setting.objects:
            cur = state.object_state.get(obj.id)
            if cur in _LOCKED_STATES and obj.interactable:
                pid = players[idx % len(players)]
                assigned.append(f"{obj.id} -> {self._name(state, pid)}")
                idx += 1
        return assigned

    def _functional_role_for(self, player_id: str, state: GameState) -> str:
        """Assign rotating functional roles independent of character flavor."""
        pids = [p.id for p in state.setting.players]
        if not pids or player_id not in pids:
            return "Explorer"
        role = [
            "Explorer — discover new rooms/objects/clues",
            "Solver — apply known clues/items/codes",
            "Critic — challenge loops/contradictions and force a better option",
        ][pids.index(player_id) % 3]
        return role

    def _extract_known_codes(self, player_id: str, state: GameState) -> tuple[list[str], list[str]]:
        """Extract known numeric and phrase codes from private clues + discoveries."""
        numbers: list[str] = []
        phrases: list[str] = []

        def ingest(text: str) -> None:
            # Intentionally no word-boundaries: clues like "locker_code_0451"
            # should still yield the numeric token "0451". Minimum length 2 so
            # short keypad codes like "99" are captured too (length-matched later
            # against the lock's code_digits when known).
            for token in re.findall(r"[0-9]{2,8}", text):
                numbers.append(token)
            # Alphabetic / alphanumeric code tokens like "ABC" (e.g. revealed as
            # "code_ABC"). Case is preserved because keypad codes are usually
            # case-sensitive. Requires 2+ consecutive uppercase letters so we do
            # not grab ordinary capitalized words ("The", "Grid").
            for token in re.findall(r"[A-Z][A-Z0-9]{1,7}", text):
                phrases.append(token)
            low = text.lower()
            if "reroute auxiliary" in low:
                phrases.append("reroute auxiliary")
            # Generic quoted phrases can also be actionable text codes.
            for quoted in re.findall(r"'([^']{3,40})'", text):
                q = quoted.strip().lower()
                if q and not q.isdigit():
                    phrases.append(q)

        # Player-private clues are known to that player even before team discovery.
        for clue in state.setting.player_clues:
            if clue.player_id == player_id:
                ingest(clue.clue)
        for info in sorted(state.discovered_info):
            ingest(info)

        def dedupe(values: list[str]) -> list[str]:
            out: list[str] = []
            for value in values:
                if value not in out:
                    out.append(value)
            return out

        return dedupe(numbers), dedupe(phrases)

    def _codes_for_object(
        self,
        obj,
        numeric_codes: list[str],
        phrase_codes: list[str],
    ) -> list[str]:
        """Return only code candidates that match the lock's expected format."""
        if not obj.requires_code:
            return []
        # Numeric code lock: only suggest digit codes, length-matched when known.
        if obj.code_digits:
            filtered = [c for c in numeric_codes if len(c) == obj.code_digits]
            return filtered[:2]
        # Textual/unknown-format lock: phrase first, then numeric fallback.
        return (phrase_codes + numeric_codes)[:2]

    def _compile_candidates(
        self,
        player_id: str,
        state: GameState,
        current_goal: str,
        next_plan_step: str = "",
    ) -> list[CandidateAction]:
        """Deterministically compile a small, valid action policy set."""
        out: list[CandidateAction] = []
        seen: set[str] = set()
        world_key = world_fingerprint(state)
        visible = state.visible_objects_for(player_id)
        inventory = list(state.player_inventories.get(player_id, []))
        numeric_codes, phrase_codes = self._extract_known_codes(player_id, state)
        focus_id = _goal_object_id(current_goal)
        plan_low = next_plan_step.lower().replace("_", " ").strip()

        def push(action: GameAction, tag: str) -> None:
            sig = action_signature(action)
            if sig in seen:
                return
            # Never propose actions already blocked in this exact world.
            if self.blocked_reason(player_id, action, world_key, state):
                return
            seen.add(sig)
            out.append(CandidateAction(action=action, tag=tag))

        def plan_match(action: GameAction) -> bool:
            if not plan_low:
                return False
            sig = action_signature(action).lower().replace("_", " ")
            return sig in plan_low or any(tok in sig for tok in plan_low.split() if len(tok) > 3)

        # Goal-targeted candidates first.
        if focus_id and focus_id in visible:
            focus_obj = state.obj(focus_id)
            if focus_obj is not None:
                for code in self._codes_for_object(focus_obj, numeric_codes, phrase_codes):
                    push(
                        GameAction(
                            action=GameActionType.ENTER_CODE,
                            target_id=focus_id,
                            code=code,
                        ),
                        "PROGRESS",
                    )
                # USE candidate when the object has a matching tool requirement.
                if focus_obj.requires_tool and focus_obj.requires_tool in inventory:
                    push(
                        GameAction(
                            action=GameActionType.USE,
                            item_id=focus_obj.requires_tool,
                            target_id=focus_id,
                        ),
                        "PROGRESS",
                    )
                # SET_FUSE candidate when the object is a fuse panel.
                if focus_obj.fuses is not None and focus_id in state.fuse_state:
                    for fuse, pos in state.fuse_state[focus_id].items():
                        if pos != "ON":
                            push(
                                GameAction(
                                    action=GameActionType.SET_FUSE,
                                    target_id=focus_id,
                                    fuse=fuse,
                                    position="ON",
                                ),
                                "PROGRESS",
                            )
                # TAKE candidate when the goal is to pick up this item.
                if focus_obj.takeable and state.object_state.get(focus_id) not in (
                    ObjectState.TAKEN,
                    ObjectState.HIDDEN,
                ):
                    push(
                        GameAction(action=GameActionType.TAKE, target_id=focus_id),
                        "PROGRESS",
                    )

        # Movement through open exits is usually progress-making.
        for _door, to_room, is_open in state.exits_for(player_id):
            if is_open:
                push(GameAction(action=GameActionType.MOVE, to_room=to_room), "PROGRESS")

        for oid in visible:
            obj = state.obj(oid)
            if obj is None:
                continue
            # Skip re-inspecting objects already examined (inspect is idempotent),
            # so the policy stops looping on decoys and stale flavor objects.
            if oid not in self._inspected_targets:
                push(GameAction(action=GameActionType.INSPECT, target_id=oid), "EXPLORATION")
            if obj.takeable and state.object_state.get(oid) != ObjectState.TAKEN:
                push(GameAction(action=GameActionType.TAKE, target_id=oid), "PROGRESS")

            if obj.requires_code and state.object_state.get(oid) in _LOCKED_STATES:
                for code in self._codes_for_object(obj, numeric_codes, phrase_codes):
                    push(
                        GameAction(
                            action=GameActionType.ENTER_CODE,
                            target_id=oid,
                            code=code,
                        ),
                        "PROGRESS",
                    )

            # Only propose USE when deterministic requirements indicate it is plausible.
            if obj.requires_tool and obj.requires_tool in inventory:
                push(
                    GameAction(
                        action=GameActionType.USE,
                        item_id=obj.requires_tool,
                        target_id=oid,
                    ),
                    "PROGRESS",
                )

            # Fuse toggles are deterministic progress actions for power puzzles.
            if obj.fuses is not None and oid in state.fuse_state:
                for fuse, pos in state.fuse_state[oid].items():
                    if pos != "ON":
                        push(
                            GameAction(
                                action=GameActionType.SET_FUSE,
                                target_id=oid,
                                fuse=fuse,
                                position="ON",
                            ),
                            "PROGRESS",
                        )

        push(
            GameAction(
                action=GameActionType.SAY,
                message="quick update: one clue or blocker",
            ),
            "COORDINATION",
        )

        # Shared-plan bias: if any candidate matches the next open plan step,
        # move those to the front so the LLM sees executable plan continuity.
        if plan_low:
            matched = [c for c in out if plan_match(c.action)]
            rest = [c for c in out if not plan_match(c.action)]
            out = matched + rest
        return out[:8]

    def _describe_candidate(self, c: CandidateAction) -> str:
        """Human-readable candidate line for prompts/UI."""
        a = c.action
        tag = f"[{c.tag}] "
        if a.action == GameActionType.MOVE and a.to_room:
            return f"{tag}move to {a.to_room}"
        if a.action == GameActionType.INSPECT and a.target_id:
            return f"{tag}inspect {a.target_id}"
        if a.action == GameActionType.TAKE and a.target_id:
            return f"{tag}take {a.target_id}"
        if a.action == GameActionType.ENTER_CODE and a.target_id and a.code:
            return f"{tag}enter_code {a.target_id} with {a.code}"
        if a.action == GameActionType.USE and a.item_id and a.target_id:
            return f"{tag}use {a.item_id} on {a.target_id}"
        if a.action == GameActionType.SET_FUSE and a.target_id and a.fuse and a.position:
            return f"{tag}set_fuse {a.target_id} {a.fuse} {a.position}"
        if a.action == GameActionType.SAY:
            return f"{tag}say one short clue/blocker update"
        return f"{tag}{action_signature(a)}"

    def is_policy_candidate(
        self,
        player_id: str,
        state: GameState,
        current_goal: str,
        action: GameAction,
        next_plan_step: str = "",
    ) -> bool:
        """True when an action is in the deterministic policy candidate set."""
        if action.action in _FREE_ACTIONS:
            return True
        allowed = {
            action_signature(c.action)
            for c in self._compile_candidates(player_id, state, current_goal, next_plan_step)
        }
        return action_signature(action) in allowed

    def policy_candidates(
        self,
        player_id: str,
        state: GameState,
        current_goal: str,
        next_plan_step: str = "",
    ) -> list[GameAction]:
        """Deterministic valid/reachable action candidates for this turn."""
        return [
            c.action
            for c in self._compile_candidates(
                player_id,
                state,
                current_goal,
                next_plan_step,
            )
        ]

    def _candidate_actions(
        self,
        player_id: str,
        state: GameState,
        current_goal: str,
        next_plan_step: str = "",
    ) -> list[str]:
        """Render policy candidates for the prompt (small, tagged, deterministic)."""
        return [
            self._describe_candidate(c)
            for c in self._compile_candidates(player_id, state, current_goal, next_plan_step)
        ]

    def _next_plan_step(self) -> str:
        """Return the first unresolved shared-plan step, if any."""
        for step in self.plan.steps:
            if not step.done:
                return step.text
        return ""

    def critical_guidance_for(self, player_id: str, state: GameState) -> str | None:
        """Escalate when an agent loops on low-value actions while blockers remain."""
        board = derive_board(state)
        current_goal = derive_current_goal(board)
        blockers = self._blockers_for_goal(state, current_goal)
        if not blockers:
            return None

        recent = [a for a in self.attempts if a.player_id == player_id]
        if not recent:
            return None

        # Avoid emitting the same critical warning every single turn.
        if self._last_critical_turn.get(player_id) == state.turn:
            return None

        r1 = recent[-1].signature
        say_loop = r1.startswith("say")
        inspect_loop = (
            len(recent) >= 3
            and all(a.signature.startswith("inspect") for a in recent[-3:])
            and len({a.signature for a in recent[-3:]}) == 1
        )
        if not (say_loop or inspect_loop):
            return None

        # Build a specific directive using known code + matching lock when possible.
        numeric_codes, phrase_codes = self._extract_known_codes(player_id, state)
        known_codes = numeric_codes + phrase_codes
        for obj in state.setting.objects:
            cur = state.object_state.get(obj.id)
            # Only nudge toward a lock the player can actually reach/see right now,
            # otherwise the agent gets pushed into an unreachable "can't reach" move.
            if (
                obj.requires_code
                and cur in _LOCKED_STATES
                and known_codes
                and state.is_visible_to(obj.id, player_id)
            ):
                self._last_critical_turn[player_id] = state.turn
                return (
                    f"The team is looping. You know code '{known_codes[0]}'. "
                    f"Use enter_code on '{obj.id}' now to progress."
                )

        self._last_critical_turn[player_id] = state.turn
        return (
            "The team is looping with blockers active. Stop repeating speech/inspect. "
            "Pick a non-redundant candidate action that unlocks state now."
        )

    def _team_memory(self, state: GameState) -> list[str]:
        """Deterministic compact memory for small models (no free-form CoT)."""
        out: list[str] = []
        for info in sorted(state.discovered_info):
            out.append(f"Confirmed clue: {info}")
        for token in sorted(self.milestones):
            if token.startswith("opened:") or token.startswith("took:") or token.startswith("power:"):
                out.append(_humanize(token))
        # Include the most recent unique failed outcomes as blocker memory.
        seen: set[str] = set()
        for a in reversed(self.attempts):
            if a.success:
                continue
            msg = a.outcome.strip()
            if not msg or msg in seen:
                continue
            seen.add(msg)
            out.append(f"Failed before: {msg}")
            if len(out) >= 8:
                break
        return out[:8]

    def _blockers_for_goal(self, state: GameState, current_goal: str) -> list[str]:
        """Derive concise blockers for the current goal from object requirements and failures."""
        blockers: list[str] = []
        # Requirement-based blockers from locked-object line.
        if " is still " in current_goal:
            _, _, right = current_goal.partition(" is still ")
            if "(needs" in right:
                hint = right[right.find("(") :].strip()
                blockers.append(hint.strip("()"))
        # Recent concrete simulator failures help the model pivot quickly.
        for a in reversed(self.attempts):
            if a.success:
                continue
            txt = a.outcome.lower()
            if "no open route" in txt:
                blockers.append("No open route from current room")
            elif "can't see" in txt:
                blockers.append("Target not visible from current room")
            elif "locked" in txt:
                blockers.append("A required lock is still closed")
            if len(blockers) >= 4:
                break
        # De-duplicate while preserving order.
        uniq: list[str] = []
        for b in blockers:
            if b and b not in uniq:
                uniq.append(b)
        return uniq[:4]

    def _derive_positional_goal(self, player_id: str, state: GameState, board: SolutionBoard) -> str:
        """Return the most actionable goal from the player's current position.

        Priority:
          1. Fuse panels in the current room with unset fuses (power activation).
          2. Locked interactable objects in the current room that have a solvable
             requirement (code / tool / power).
          3. Takeable items in the current room not yet collected.
          4. Board-derived goal as fallback (catches the win condition, etc.).

        Using position-aware derivation ensures the model sees the correct
        *immediate* action (e.g. "set fuse MAIN ON on server_main_console") rather
        than the far-away global goal ("final_container_lock is still locked").
        """
        room = state.player_locations.get(player_id, "")
        inventory = set(state.player_inventories.get(player_id, []))

        # Priority 1: fuse panels with at least one OFF fuse.
        for obj in state.setting.objects:
            if (
                obj.fuses is not None
                and state.room_of(obj.id) == room
                and obj.id in state.fuse_state
                and state.is_visible_to(obj.id, player_id)
            ):
                for fuse, pos in state.fuse_state[obj.id].items():
                    if pos != "ON":
                        return (
                            f"set fuse {fuse} to ON on {obj.id} to activate power"
                        )

        # Priority 2: locked interactable objects in the current room with
        # a defined unlock mechanism (filters out un-solvable decoys).
        for obj in state.setting.objects:
            if (
                state.room_of(obj.id) == room
                and obj.interactable
                and state.object_state.get(obj.id) in _LOCKED_STATES
                and state.is_visible_to(obj.id, player_id)
                and _has_requirement(obj)
            ):
                cur = state.object_state[obj.id]
                return f"{obj.id} is still {cur.value}{_requirement_hint(obj)}"

        # Priority 3: takeable items in the room not yet in inventory.
        for obj in state.setting.objects:
            if (
                obj.takeable
                and state.room_of(obj.id) == room
                and obj.id not in inventory
                and state.object_state.get(obj.id)
                not in (ObjectState.TAKEN, ObjectState.HIDDEN)
                and state.is_visible_to(obj.id, player_id)
            ):
                return f"take {obj.id} from current room"

        # Fallback: global board derivation (captures win condition etc.).
        return derive_current_goal(board)

    def brief_for(
        self,
        player_id: str,
        state: GameState,
        *,
        reflect: bool = False,
        blocked_note: str | None = None,
        critical_note: str | None = None,
        recommended_action: str = "",
    ) -> TeamBrief:
        teammate_ideas = [
            f"{self._name(state, pid)}: {idea}"
            for pid, idea in self.hypotheses.items()
            if pid != player_id and idea
        ][-self.config.hypothesis_limit:]

        board = derive_board(state)
        # Sync milestones from the current authoritative state so plan refresh
        # works correctly even when observe() hasn't been called (e.g. tests).
        self.milestones = compute_milestones(state)
        # Sync the shared plan against the full cumulative milestone set each turn.
        # This uses milestone-type-aware ticking so steps like "Use X on Y" are not
        # prematurely ticked just because item X was taken.
        self.plan.refresh(self.milestones)
        next_plan_step = self._next_plan_step()
        # Position-aware goal: shows the most proximate actionable goal instead of
        # the global final objective.
        current_goal = self._derive_positional_goal(player_id, state, board)

        return TeamBrief(
            objective=board.objective,
            current_goal=current_goal,
            blockers=self._blockers_for_goal(state, current_goal),
            solved=board.solved,
            open_puzzles=board.unsolved,
            ownership=self._ownership(state),
            team_memory=self._team_memory(state),
            candidate_actions=self._candidate_actions(
                player_id,
                state,
                current_goal,
                next_plan_step,
            ),
            recommended_action=recommended_action,
            already_examined=self._examined_here(player_id, state),
            teammate_locations=self._teammate_locations(player_id, state),
            next_plan_step=next_plan_step,
            functional_role=self._functional_role_for(player_id, state),
            teammate_hypotheses=teammate_ideas,
            failed_attempts=self._blocked_for(player_id, state),
            critical_note=critical_note,
            stuck=self.stuck,
            reflect=reflect,
            blocked_note=blocked_note,
        )

    def _examined_here(self, player_id: str, state: GameState) -> list[str]:
        """Objects in the player's view already examined (don't re-inspect)."""
        return [
            oid
            for oid in state.visible_objects_for(player_id)
            if oid in self._inspected_targets
        ]

    def _teammate_locations(self, player_id: str, state: GameState) -> list[str]:
        """Where each teammate currently is (room), for coordination."""
        here = state.player_locations.get(player_id)
        out: list[str] = []
        for pid, room in state.player_locations.items():
            if pid == player_id:
                continue
            tag = "same room" if room == here else room
            out.append(f"{self._name(state, pid)} is in {tag}")
        return out

    @staticmethod
    def _name(state: GameState, player_id: str) -> str:
        for p in state.setting.players:
            if p.id == player_id:
                return p.name
        return player_id
