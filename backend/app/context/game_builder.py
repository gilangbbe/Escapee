"""
Context builder (v2 — GameSetting engine). Anti-forgetting for small (7B) models.

Each turn we rebuild the player's prompt context from the AUTHORITATIVE
`GameState` so the model can never drift. Fixed sections, in priority order:

  1. PERSONA + OBJECTIVE  (re-pinned every turn — in the system prompt)
  2. CURRENT STATE VIEW    (room, visible objects, inventory, exits) — from GameState
  3. PRIVATE CLUES         (only this player's asymmetric knowledge)
  4. ROLLING SUMMARY       (compressed older history)
  5. RECENT EVENTS         (last K turns verbatim: speech + observations)

Older events beyond the recent window fold into the rolling summary so the
context never overflows the 7B window. The summarizer is deterministic (no LLM)
and reused from the v1 context module.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.context.builder import render_recent_events, summarize_events  # reused
from app.context.channels import MessageLog
from app.engine.game_state import GameState

__all__ = [
    "GameStateView",
    "TeamBrief",
    "build_game_state_view",
    "render_recent_events",
    "summarize_events",
]


@dataclass
class TeamBrief:
    """Shared, cross-player collaboration state for a single turn.

    Assembled by the external-cognition layer (`TeamCognition`) so each agent can
    reason with memory rather than act in isolation: teammates' latest proposals,
    moves that are now provably pointless, the milestones already achieved,
    untried reachable objects, a summarized team memory, and whether the team is
    stuck / due for a reflection checkpoint.
    """

    teammate_hypotheses: list[str]      # "Name: proposed idea" (teammates only)
    failed_attempts: list[str]          # "signature — why it failed" (blocked now)
    objective: str = ""                 # the team goal (ground truth)
    current_goal: str = ""              # single next actionable goal
    blockers: list[str] = field(default_factory=list)          # blockers for current goal
    solved: list[str] = field(default_factory=list)            # already-done facts
    open_puzzles: list[str] = field(default_factory=list)      # still-to-do facts
    ownership: list[str] = field(default_factory=list)         # explicit task ownership
    team_memory: list[str] = field(default_factory=list)       # concise deterministic memory
    candidate_actions: list[str] = field(default_factory=list) # valid/reachable next actions
    next_plan_step: str = ""                # next open step from shared debated plan
    functional_role: str = ""              # explorer | solver | critic
    critical_note: str | None = None        # orchestrator-injected anti-loop warning
    stuck: bool = False
    reflect: bool = False
    blocked_note: str | None = None     # set when re-deciding after a blocked move

    def render(self) -> str:
        ideas = "\n".join(f"- {h}" for h in self.teammate_hypotheses) or (
            "- (no teammate proposals yet — share yours)"
        )
        fails = "\n".join(f"- {f}" for f in self.failed_attempts) or "- (none yet)"
        goal = self.current_goal.strip() or "(derive a next actionable step from the board)"
        blockers = "\n".join(f"- {b}" for b in self.blockers) or "- (none listed)"
        memory = "\n".join(f"- {m}" for m in self.team_memory) or "- (none yet)"
        owners = "\n".join(f"- {o}" for o in self.ownership) or "- (unassigned)"
        actions = "\n".join(f"- {a}" for a in self.candidate_actions) or "- (no clear candidate yet)"
        solved = "; ".join(self.solved) or "(nothing yet)"
        todo = "; ".join(self.open_puzzles) or "(none — you may be ready to win)"

        sections = []
        if self.critical_note:
            sections.append(f"🚨 CRITICAL: {self.critical_note}\n")
        if self.blocked_note:
            sections.append(f"⛔ {self.blocked_note}\n")
        if self.stuck:
            sections.append(
                "⚠ THE TEAM IS STUCK. Stop repeating actions. Explore an untried "
                "reachable object or tackle a listed blocker.\n"
            )
        if self.reflect:
            sections.append(
                "🧠 REFLECTION CHECKPOINT. In \"reflection\", briefly summarize what is "
                "confirmed, what is blocked, and the single best next step before you act.\n"
            )
        sections.append(
            "=== CURRENT GOAL (ground truth from state) ===\n"
            f"GOAL: {self.objective}\n"
            f"CURRENT GOAL: {goal}\n"
            f"BLOCKERS:\n{blockers}"
        )
        sections.append(f"TEAM MEMORY (confirmed facts only):\n{memory}")
        if self.next_plan_step:
            sections.append(f"NEXT SHARED PLAN STEP:\n- {self.next_plan_step}")
        if self.functional_role:
            sections.append(f"FUNCTIONAL ROLE THIS TURN: {self.functional_role}")
        sections.append(f"ACTIVE OWNERSHIP:\n{owners}")
        sections.append(f"CANDIDATE ACTIONS (valid/reachable/progress-biased):\n{actions}")
        sections.append(
            "OBJECTIVE BOARD:\n"
            f"✅ SOLVED (already done — NEVER redo): {solved}\n"
            f"🔲 STILL TO DO (unsolved objectives): {todo}"
        )
        sections.append(
            f"TEAMMATE PROPOSALS (support, critique, or counter them):\n{ideas}"
        )
        sections.append(
            "DO NOT REPEAT (already tried / already done):\n"
            f"{fails}"
        )
        return "\n\n".join(sections)




@dataclass
class GameStateView:
    """A grounded, player-specific snapshot derived from GameState (v2)."""

    player_id: str
    room_id: str
    visible_objects: list[str]              # object ids
    inventory: list[str]                    # object ids held
    exits: list[tuple[str, str, bool]]      # (door_id, to_room, is_open)
    other_players_here: list[str]

    def render(self) -> str:
        objs = ", ".join(f"[{o}]" for o in self.visible_objects) or "nothing notable"
        inv = ", ".join(f"[{i}]" for i in self.inventory) or "empty-handed"
        if self.exits:
            exit_str = ", ".join(
                f"{to} via [{door}] ({'open' if op else 'locked'})"
                for door, to, op in self.exits
            )
        else:
            exit_str = "none"
        others = ", ".join(self.other_players_here) or "you are alone"
        return (
            f"LOCATION: {self.room_id}\n"
            f"OBJECTS YOU CAN SEE: {objs}\n"
            f"YOUR INVENTORY: {inv}\n"
            f"EXITS: {exit_str}\n"
            f"OTHER PLAYERS HERE: {others}"
        )


def build_game_state_view(state: GameState, player_id: str) -> GameStateView:
    """Derive the current grounded state view for one player from GameState."""
    room_id = state.player_locations[player_id]
    visible = state.visible_objects_for(player_id)
    inventory = list(state.player_inventories.get(player_id, []))
    exits = state.exits_for(player_id)

    others = [
        _player_name(state, pid)
        for pid, loc in state.player_locations.items()
        if pid != player_id and loc == room_id
    ]

    return GameStateView(
        player_id=player_id,
        room_id=room_id,
        visible_objects=visible,
        inventory=inventory,
        exits=exits,
        other_players_here=others,
    )


def _player_name(state: GameState, player_id: str) -> str:
    for p in state.setting.players:
        if p.id == player_id:
            return p.name
    return player_id


def seed_private_clues(log: MessageLog, state: GameState) -> None:
    """Load each player's asymmetric clues from the setting into the log."""
    for clue in state.setting.player_clues:
        log.add_clue(clue.player_id, clue.clue)
