"""
World Normalizer — deterministic repair layer between the LLM world-builder
and the game engine.

The world-building layer (multi-agent LLM) consistently produces structurally
inconsistent worlds: broken reference chains, semantic tag mismatches, and
hallucinated solution paths. Rather than fighting the generator or burdening
the engine with defensive fallbacks, this module repairs any world to a
playable state before the engine ever sees it.

Pipeline position:
    raw JSON → FixedWorldEnvelope → [WorldNormalizer] → GameSetting → engine

Repairs applied (in order):

  R1  Functional-scenic
      Objects with interactable=False but carrying engine-relevant fields
      (contains_info, fuses, requires_*, reveals, provides_power) are promoted
      to interactable=True. The engine gates all player interaction on
      interactable; scenic objects that carry these fields are permanently
      inaccessible without this fix.

  R2  Tool not takeable
      If object A declares requires_tool=B, and B is not takeable, B is marked
      takeable=True. The engine satisfies requires_tool by checking that the
      tool is in the player's inventory; a non-takeable tool can never get
      there, making the puzzle silently unsolvable.

  R3  requires_code = object_id
      When a world author writes requires_code pointing at a known object id
      (intending "you need info from that object"), the engine's exact-match
      check will never succeed because agents enter info tokens, not object
      ids. This repair looks up the referenced object's contains_info token
      and replaces the bad requires_code with the actual token. If the
      referenced object has no contains_info, the issue is logged and left for
      manual review (we cannot safely invent a code).

  R4  Hidden with no reveal
      Objects with state=hidden that no other object reveals are permanently
      inaccessible. They are promoted to state=visible. This is safe: the
      object was intended to be found; it just has no explicit trigger.

  R5  Derived solution_path (replaces LLM-authored freetext)
      The actual minimal action sequence to reach the win condition is computed
      via backward-recursive dependency resolution through the object
      requirement graph. This replaces the LLM-authored solution_path, which
      frequently contains placeholder text, references non-existent objects, or
      guides agents toward dead-end branches. The derived path is what
      _plan_phase uses as a fallback and what the action planner uses as
      guidance.

  R6  Functional-first object ordering
      The ActionPlanner evaluates only the first beam_width=5 candidates from
      policy_candidates(). policy_candidates() iterates visible_objects_for()
      which follows the order of setting.objects. When many scenic filler
      objects precede functional ones in the world JSON, info-producers like
      scenic_rotting_fabric (contains_info) are pushed past position 5 and
      the planner never considers inspecting them — so it keeps recommending
      wrong ENTER_CODE attempts. This repair sorts objects so that info-
      producers come first (tier 0), then other functional objects (tier 1),
      then takeable items (tier 2), then pure decoration (tier 3). The sort is
      stable: relative order within each tier is preserved.

  R7  Numeric requires_code with no info-chain → relink to info token
      The world-builder sometimes writes requires_code as a bare number (e.g.
      "321") while the only discoverable clue is an info token ending in that
      number (e.g. "symbol_code_321"). Agents learn "symbol_code_321" from
      inspecting the source object but the engine exact-matches against "321"
      — a guaranteed failure. Detected by checking whether any contains_info
      token's numeric suffix (the part after the last "_") matches the bare
      number. When exactly one such token exists, requires_code is updated to
      the full token so agents enter what they discover and the match
      succeeds. Also fixes the derived solution_path which could not trace the
      info-chain without the full token.

All repairs are logged so you can audit what the generator got wrong and
feed patterns back into the world-building layer's policy over time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Object fields that make an object engine-relevant (functionally interactive).
_FUNCTIONAL_FIELDS = frozenset({
    "contains_info",
    "fuses",
    "requires_code",
    "requires_tool",
    "requires_power",
    "requires_liquid",
    "provides_power",
    "reveals",
})


@dataclass
class NormalizeResult:
    """Outcome of a normalize_world() call."""

    objects: list[dict]
    solution_path: list[str]
    repairs: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ #
# Public entry point
# ------------------------------------------------------------------ #

def normalize_world(
    objects: list[dict],
    win_condition: dict,
    room_ids: list[str],
    *,
    original_solution_path: list[str] | None = None,
) -> NormalizeResult:
    """Run all repair passes on raw object dicts, then derive solution path.

    Parameters
    ----------
    objects:
        Raw object dicts from FixedObject.model_dump(), after _normalize_code
        has already run (numeric symbolic codes already reduced).
    win_condition:
        Raw dict with at minimum ``object_id`` and ``state`` keys.
    room_ids:
        Ordered list of room ids from the world (used for room-of lookups in
        the derived solution path).
    original_solution_path:
        The LLM-authored path (freetext). Presence is noted in the repair log
        for audit purposes; it is always replaced by the derived path.

    Returns
    -------
    NormalizeResult
        Repaired object list, derived solution_path, and human-readable repair
        log entries (one string per repair applied).
    """
    repairs: list[str] = []

    # Work on shallow copies — callers keep their originals.
    objs: list[dict] = [dict(o) for o in objects]
    by_id: dict[str, dict] = {o["id"]: o for o in objs}
    room_id_set: set[str] = set(room_ids)

    _repair_functional_scenic(objs, by_id, repairs)
    _repair_tool_not_takeable(objs, by_id, repairs)
    _repair_requires_code_is_object_id(objs, by_id, repairs)
    _repair_hidden_no_reveal(objs, by_id, repairs)
    _repair_numeric_code_no_info_chain(objs, by_id, repairs)

    # R6: reorder so info-producers appear before consumers before pure scenery.
    # by_id values are the same dict objects — lookup remains valid after sort.
    objs = _sort_objects_for_beam(objs, repairs)

    solution_path = _derive_solution_path(objs, win_condition, by_id, room_id_set)

    if original_solution_path:
        repairs.append(
            f"R5 solution_path: replaced LLM-authored path "
            f"({len(original_solution_path)} step(s)) with derived path "
            f"({len(solution_path)} step(s))"
        )

    if repairs:
        log.info(
            "World normalizer: %d repair(s) applied:\n%s",
            len(repairs),
            "\n".join(f"  • {r}" for r in repairs),
        )
    else:
        log.debug("World normalizer: no repairs needed.")

    return NormalizeResult(objects=objs, solution_path=solution_path, repairs=repairs)


# ------------------------------------------------------------------ #
# Repair passes (R1–R4)
# ------------------------------------------------------------------ #

def _repair_functional_scenic(
    objs: list[dict], by_id: dict[str, dict], repairs: list[str]
) -> None:
    """R1: Promote non-interactable objects that carry functional engine fields."""
    for obj in objs:
        if obj.get("interactable", True):
            continue  # already interactable
        if any(obj.get(f) for f in _FUNCTIONAL_FIELDS):
            obj["interactable"] = True
            repairs.append(
                f"R1 '{obj['id']}': promoted to interactable=True "
                f"(marked scenic/non-interactable but carries functional fields: "
                f"{[f for f in _FUNCTIONAL_FIELDS if obj.get(f)]})"
            )


def _repair_tool_not_takeable(
    objs: list[dict], by_id: dict[str, dict], repairs: list[str]
) -> None:
    """R2: Tools referenced by requires_tool must be takeable (engine needs inventory)."""
    for obj in objs:
        req_tool = obj.get("requires_tool")
        if not req_tool:
            continue
        tool = by_id.get(req_tool)
        if tool is not None and not tool.get("takeable", False):
            tool["takeable"] = True
            repairs.append(
                f"R2 '{req_tool}': set takeable=True "
                f"(used as requires_tool by '{obj['id']}' but was not takeable; "
                f"engine needs it in inventory to satisfy USE)"
            )


def _repair_requires_code_is_object_id(
    objs: list[dict], by_id: dict[str, dict], repairs: list[str]
) -> None:
    """R3: requires_code = object_id → replace with that object's contains_info token."""
    for obj in objs:
        req_code = obj.get("requires_code")
        if not req_code:
            continue
        if req_code not in by_id:
            continue  # not an object id — leave as-is (could be a real code/token)

        target = by_id[req_code]
        info_token = target.get("contains_info")
        if info_token:
            obj["requires_code"] = info_token
            repairs.append(
                f"R3 '{obj['id']}': requires_code '{req_code}' was an object id; "
                f"replaced with its contains_info token '{info_token}'"
            )
        else:
            # Referenced object exists but has no info to reveal — can't auto-fix.
            repairs.append(
                f"R3 '{obj['id']}': requires_code '{req_code}' is an object id "
                f"but that object has no contains_info — UNRESOLVED "
                f"(manual world review needed)"
            )


def _repair_hidden_no_reveal(
    objs: list[dict], by_id: dict[str, dict], repairs: list[str]
) -> None:
    """R4: Hidden objects with no reveal pointer are permanently inaccessible → visible."""
    reveal_targets: set[str] = {o["reveals"] for o in objs if o.get("reveals")}
    for obj in objs:
        if obj.get("state") == "hidden" and obj["id"] not in reveal_targets:
            obj["state"] = "visible"
            repairs.append(
                f"R4 '{obj['id']}': promoted from state=hidden to state=visible "
                f"(no object reveals it — would be permanently inaccessible)"
            )


def _repair_numeric_code_no_info_chain(
    objs: list[dict], by_id: dict[str, dict], repairs: list[str]
) -> None:
    """R7: Bare-digit requires_code with no direct info-chain → relink to info token.

    The world-builder sometimes writes `requires_code = "321"` (a plain number)
    while the only discoverable clue is `contains_info = "symbol_code_321"`. Agents
    learn "symbol_code_321" but the engine exact-matches against "321" — failure.

    Detection: for each object whose requires_code is purely digits and has no
    contains_info producer that exposes that exact string, scan all info tokens for
    one whose numeric suffix (part after the last "_") equals the plain code.

    If exactly one token matches → update requires_code to the full token.
    If multiple tokens match → ambiguous, log and leave unchanged.
    If none match → leave unchanged (genuinely stand-alone code, no info chain).
    """
    # Build suffix → info tokens map: e.g. "321" → ["symbol_code_321"]
    suffix_to_tokens: dict[str, list[str]] = {}
    for obj in objs:
        token = obj.get("contains_info")
        if not isinstance(token, str):
            continue
        parts = token.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            suffix_to_tokens.setdefault(parts[1], []).append(token)

    # Build direct-chain set: info tokens already used verbatim as requires_code.
    direct_chain: set[str] = {o["contains_info"] for o in objs if o.get("contains_info")}

    for obj in objs:
        req_code = obj.get("requires_code")
        if not isinstance(req_code, str) or not req_code.isdigit():
            continue
        # Chain already coherent: some object has contains_info == req_code verbatim.
        if req_code in direct_chain:
            continue
        matching = suffix_to_tokens.get(req_code, [])
        if len(matching) == 1:
            new_code = matching[0]
            obj["requires_code"] = new_code
            repairs.append(
                f"R7 '{obj['id']}': requires_code '{req_code}' is a bare number with no "
                f"direct info-chain; relinked to info token '{new_code}' "
                f"(agents discover and enter the full token)"
            )
        elif len(matching) > 1:
            repairs.append(
                f"R7 '{obj['id']}': requires_code '{req_code}' matches multiple info "
                f"tokens {matching} — ambiguous, left unchanged (manual review needed)"
            )
        # Zero matches → stand-alone digit code, no action needed.


# ------------------------------------------------------------------ #
# R6 — Functional-first object ordering
# ------------------------------------------------------------------ #

def _sort_objects_for_beam(
    objs: list[dict], repairs: list[str]
) -> list[dict]:
    """R6: Sort objects so info-producers appear before consumers before pure scenery.

    The ActionPlanner beam (width=5) only evaluates the first 5 candidates from
    policy_candidates(). policy_candidates() iterates visible_objects_for() which
    follows setting.objects order. Functional objects (especially info-producers
    like scenic_rotting_fabric that carry contains_info) buried under many scenic
    fillers are pushed past position 5 and the planner never considers them.

    Tier 0 — info producers (contains_info): must be inspected to reveal codes;
              highest priority so the planner discovers them before attempting
              wrong ENTER_CODE candidates.
    Tier 1 — other functional objects (requires_*, fuses, reveals, provides_power):
              puzzles, containers, power sources.
    Tier 2 — takeable items with no functional fields: potential tools.
    Tier 3 — pure decoration: no functional fields, not takeable.

    The sort is stable: relative order within each tier is preserved.
    """
    def _tier(obj: dict) -> int:
        if obj.get("contains_info"):
            return 0
        if any(obj.get(f) for f in _FUNCTIONAL_FIELDS):
            return 1
        if obj.get("takeable", False):
            return 2
        return 3

    sorted_objs = sorted(objs, key=_tier)

    # Log only if the order actually changed.
    original_order = [o["id"] for o in objs]
    new_order = [o["id"] for o in sorted_objs]
    if new_order != original_order:
        promoted = [oid for oid in new_order if _tier(objs[original_order.index(oid)]) < 3
                    and original_order.index(oid) != new_order.index(oid)]
        repairs.append(
            f"R6 reordered objects for planner beam: {len(promoted)} functional "
            f"object(s) moved before scenic fillers"
            + (f" (e.g. {promoted[:3]})" if promoted else "")
        )

    return sorted_objs


# ------------------------------------------------------------------ #
# R5 — Derived solution path via backward dependency resolution
# ------------------------------------------------------------------ #

def _room_of(obj_id: str, by_id: dict[str, dict], room_id_set: set[str]) -> str | None:
    """Follow the location chain to find the room an object ultimately lives in."""
    seen: set[str] = set()
    cur = obj_id
    while cur not in seen:
        seen.add(cur)
        obj = by_id.get(cur)
        if obj is None:
            return None
        loc = obj.get("location", "")
        if loc in room_id_set:
            return loc
        cur = loc
    return None


def _derive_solution_path(
    objs: list[dict],
    win_condition: dict,
    by_id: dict[str, dict],
    room_id_set: set[str],
) -> list[str]:
    """Compute the actual minimal solution path via backward dependency resolution.

    Starting from the win condition object, recursively resolves every
    requirement (requires_tool, requires_code) back to its sources, building
    forward-ordered action steps as it goes. This replaces the LLM-authored
    freetext solution_path which is frequently wrong or uses placeholder text.

    The result is a human-readable ordered list of steps suitable for both
    the _plan_phase fallback and the action planner's guidance context.
    """
    # Build info_producers: info_token → [object_ids that produce it]
    info_producers: dict[str, list[str]] = {}
    for o in objs:
        ci = o.get("contains_info")
        if ci:
            info_producers.setdefault(ci, []).append(o["id"])

    steps: list[str] = []
    resolved: set[str] = set()

    def room_hint(obj_id: str) -> str:
        r = _room_of(obj_id, by_id, room_id_set)
        return f" (in {r})" if r else ""

    def resolve(obj_id: str) -> None:
        """Recursively ensure all prerequisites for obj_id are met."""
        if obj_id in resolved:
            return
        resolved.add(obj_id)

        obj = by_id.get(obj_id)
        if obj is None:
            return

        req_tool = obj.get("requires_tool")
        req_code = obj.get("requires_code")

        if req_tool:
            # --- Tool path ---
            # Recursively satisfy whatever is needed to make req_tool available.
            resolve(req_tool)
            tool = by_id.get(req_tool)
            if tool and tool.get("takeable"):
                rh = room_hint(req_tool)
                steps.append(f"Take '{req_tool}'{rh}")
            rh = room_hint(obj_id)
            steps.append(f"Use '{req_tool}' on '{obj_id}'{rh}")

        elif req_code:
            # --- Code path ---
            # Discover the code by inspecting/opening its producer(s), then enter it.
            producers = info_producers.get(req_code, [])
            if producers:
                for prod_id in producers:
                    # Recursively satisfy the producer's own requirements first.
                    resolve(prod_id)
                    prod = by_id.get(prod_id)
                    if prod is None:
                        continue
                    rh = room_hint(prod_id)
                    prod_req_tool = prod.get("requires_tool")
                    if prod_req_tool:
                        # Info is auto-discovered when the producer is opened by its tool.
                        # The USE step was already added by resolve(prod_id) above.
                        steps.append(f"  → '{prod_id}' reveals code '{req_code}' when opened")
                    else:
                        steps.append(f"Inspect '{prod_id}'{rh} to learn '{req_code}'")
            else:
                steps.append(f"Find the code for '{obj_id}' (token: {req_code}; source unknown)")

            rh = room_hint(obj_id)
            steps.append(f"Enter code '{req_code}' on '{obj_id}'{rh}")

        # If obj has no requirement, it is freely accessible — no step needed.

    win_obj_id = win_condition.get("object_id", "")
    resolve(win_obj_id)

    if not steps:
        # Win object had no requirements — something is very simple or wrong.
        steps.append(f"Interact with '{win_obj_id}' to win")

    return steps
