# Roadmap — Multi-LLM Agent Escape Room Simulation

> Timeline of work. Update every time a task is done — mark what is complete and what remains.

Legend: ✅ done · 🚧 in progress · ⬜ not started

---

## Phase 0 — Project Foundation
- ✅ Read Overview & confirm requirements
- ✅ Verify `.venv` (Python 3.11.9)
- ✅ Create `ProjectDocument.md`
- ✅ Create `Journal.md`
- ✅ Create `Roadmap.md`
- ✅ Scaffold backend folder structure + `requirements.txt`
- ✅ Bootstrap pip + install deps into `.venv`
- ✅ Confirm local LLM serving approach (Ollama client; `qwen2.5:7b` pulled and verified end-to-end)

## Phase 1 — GM Blueprint Schema & System Prompt ✅
- ✅ Define Pydantic blueprint schema (rooms, objects, locks, items, puzzles, win conditions, per-player clues)
- ✅ Referential-integrity validator (anti-hallucination at parse time)
- ✅ Write GM system prompt + schema embedding
- ✅ JSON extraction + validation + retry/repair loop
- ✅ Ollama async client + `generate_blueprint` loop
- ✅ Sample room fixture + 11 passing tests

## Phase 2 — World Simulator & State Machine ✅
- ✅ Hand-authored canonical game: "Orbital Decay" (`app/game/orbital_decay.py`)
- ✅ Runtime state models from blueprint (`WorldState`, item-location tracking)
- ✅ `Observation` model (grounded feedback)
- ✅ `PlayerAction` schema (validated action boundary)
- ✅ Action handlers (look, inspect, move, take, give, unlock[key/code/sequence], solve, say)
- ✅ Grounded Observation generation from ground truth only
- ✅ Win-condition evaluation (reach_room / obtain_item / set_flag)
- ✅ Unit tests + full cooperative playthrough to WIN (23/23 tests pass)

## Phase 3 — Player Agent System Prompts & Tools ✅
- ✅ Player persona / asymmetric info model (reused from blueprint)
- ✅ ReAct turn schema `PlayerTurn` (Thought → Speak → Action)
- ✅ Action JSON tool schema + validation (reused `PlayerAction` + repair loop)
- ✅ Context manager: grounded `StateView`, channels (public/private), recent window + deterministic rolling summary
- ✅ Player system + user prompt assembly (persona/objective re-pinned each turn)
- ✅ `PlayerAgent.decide()` (prompt → LLM → validated turn, with repair retries)
- ✅ Phase 3 tests incl. fake-LLM agent (37/37 tests pass)

## Phase 3.5 — GameSetting v2 Schema Migration ✅
- ✅ Adopt user-provided `game_setting.json` as the authoritative GM-generated schema
- ✅ New Pydantic schema `GameSetting` (object + state + requirement model) + integrity validator
- ✅ Port "Orbital Decay" to v2 (`app/game/orbital_decay_setting.py`) with asymmetric `player_clues`
- ✅ v2 runtime `GameState` (location chains, visibility, fuse→power flags, auto-unlock)
- ✅ v2 `GameAction` + `GameSimulator` (reuses shared `Observation`)
- ✅ Retarget GM prompt + `generate_blueprint` to `GameSetting`
- ✅ v2 tests incl. full cooperative playthrough to WIN (54/54 tests pass)
- ✅ v1 code kept intact (non-destructive); `Observation` + `json_repair` shared

## Phase 4 — Orchestration Loop ✅
- ✅ Migrate player agent/context path to v2 `GameState`/`GameAction` (`game_turn`, `game_player_prompt`, `game_builder`, `game_player_agent`)
- ✅ Asyncio turn scheduler (`GameOrchestrator.run()`, round-robin ReAct cycle)
- ✅ Message passing / shared channel (reused `MessageLog`; public/private routing)
- ✅ Game lifecycle (setup → play → escape/turn-limit, `GameResult`)
- ✅ Streaming hook (injectable async `on_event` sink for Phase 5)
- ✅ Phase 4 tests incl. scripted full cooperative win through the orchestrator (66/66 tests pass)

## Phase 5 — Web App ✅
- ✅ FastAPI WebSocket endpoint (`WS /ws/game`) streaming setup/event/state/result JSON
- ✅ REST `GET /api/setting` + ASGI entrypoint `app.main:app`
- ✅ `GameRunner` + `build_agents` (per-persona Ollama agents, snapshot after each event)
- ✅ Web serializers (grounded `state_snapshot`, `event_to_dict`, `setup_message`, `result_to_dict`)
- ✅ React + Vite + TS spectator UI: scenario/crew, live narrative feed, grounded state panel
- ✅ Phase 5 tests (serializers, `GameRunner` stream, WebSocket full win) — 73/73 tests pass; `npm run build` passes

## Phase 6 — Polish ✅
- ✅ End-to-end live playthrough against Ollama (`qwen2.5:7b`) via headless `scripts/live_run.py`
- ✅ Tuning prompts for 7B reliability (bracket-id normalizer, display-only brackets, anti-loop action history, explore-open-exits + apply-clues rules)
- ✅ Docs finalization

## Phase 7 — GM Narrator (storyteller LLM) ✅
- ✅ Narrator system prompt + per-verb `describe_action` + opening/turn/ending prompt builders (`gm_narrator_prompt.py`)
- ✅ `GameMasterNarrator` with rolling memory + graceful fallback (`gm_narrator.py`)
- ✅ `EventKind.NARRATION`; orchestrator emits opening/turn/ending narration **stream-only** (`record=False`, never enters `MessageLog`)
- ✅ Runner `build_narrator` + `GameRunner(narrator=)`; WebSocket `?narrate=` param; `live_run.py --no-narrate`
- ✅ Frontend: narration event type, distinct prose rendering, narrate toggle, "Story" feed
- ✅ Test: narration streams to observers but is absent from `MessageLog` (anti-leak); live run verified against `qwen2.5:7b`

## Phase 8 — Multi-agent debate + adaptive decision-making ✅
- ✅ `hypothesis` turn field (public proposal/theory) added to `GameTurn`
- ✅ `TeamBrief` (teammate proposals + failed attempts + stuck flag) rendered into a TEAM DISCUSSION prompt section
- ✅ Orchestrator team blackboard: latest hypothesis per player, team-wide failed-attempt memory, novel-progress stall detector → one-shot "re-evaluate" SYSTEM prompt
- ✅ Hypotheses broadcast as public `(idea)` speech; `TeamBrief` threaded into `agent.decide()`
- ✅ Prompt rewrite: 10 explicit collaboration rules (share/divide/debate/don't-repeat-failures/re-evaluate)
- ✅ Tests: hypothesis broadcast, failed-attempt memory, stuck set + cleared (79/79); live run shows debate + stuck recovery

---

## Phase 9 — External cognition (memory / symbolic state / loop guard / progress / reflection) ✅
- ✅ `app/cognition/team_cognition.py` — `TeamCognition` deterministic brain: symbolic `world_fingerprint`, episodic `AttemptRecord` memory, loop guard (`is_redundant`), milestone progress ledger (intermediate rewards), stall + `should_reflect` scheduling, curiosity (untried reachable objects), summarized memory
- ✅ `Observation.hint` field + simulator hints on ambiguous failures (engine `message` unchanged; orchestrator appends hint at emit boundary only)
- ✅ `GameTurn.reflection` field + prompt rules (system BLOCKS proven repeats; stay curious; apply clues; reflect at checkpoints)
- ✅ `TeamBrief.render()` rebuilt: blocked note, stuck banner, reflection checkpoint, progress, summarized memory, proposals, DO-NOT-REPEAT, unexplored/reachable
- ✅ Orchestrator owns `TeamCognition` (replaces Phase 8 ad-hoc blackboard); loop-guard re-prompts (max 2) on redundant moves; `✓ Progress!` reward events; reflection stored as team summary
- ✅ Tests migrated to `orch.cognition.*` + new coverage (loop block, progress, fingerprint, reflection schedule); 83/83 green

---

## Phase 10 — Shared objective board + collaborative plan + goal-level no-repeat ✅
- ✅ `derive_board(state) → SolutionBoard` — ground-truth SOLVED / STILL-TO-DO board recomputed from `GameState` every turn (non-spoiler requirement hints; WIN CONDITION shown until met)
- ✅ Goal-level no-repeat: `already_done(player, action, state)` (re-take / re-code / re-use / re-move to current room) surfaced via `blocked_reason(...)`, which the orchestrator now uses in place of the raw `is_redundant`
- ✅ `TeamPlan` / `set_plan` / `plan.refresh(solved_ids)` — one ordered, auto-ticking team checklist
- ✅ Collaborative planning debate: `PlanProposal` schema + plan prompts + `agent.propose_plan(...)`; `GameOrchestrator._plan_phase()` runs propose→critique/refine before round 1, announces `📋 Shared plan agreed`, falls back to `solution_path` (gated by `enable_planning`)
- ✅ `TeamBrief` leads with `=== OBJECTIVE BOARD (ground truth) ===` (GOAL / ✅ SOLVED / 🔲 STILL TO DO / 📋 SHARED PLAN)
- ✅ 89/89 tests green (6 new); live run confirmed shared plan at t0, progress milestone, and goal-level guards firing

---

---

## Phase 11 — World Normalizer (deterministic repair layer for LLM-generated worlds) ✅
- ✅ Root-cause analysis of structural inconsistencies across worlds 015–021
- ✅ `app/engine/world_normalizer.py` — `normalize_world()` with 5 repair passes:
  - R1: functional-scenic (interactable=False + functional fields → promote to interactable)
  - R2: tool not takeable (requires_tool target not takeable → mark takeable)
  - R3: requires_code = object_id (replace with target's contains_info token)
  - R4: hidden with no reveal (state=hidden + no reveal pointer → promote to visible)
  - R5: derived solution_path (BFS backward from win_condition → replaces LLM freetext)
- ✅ `app/schemas/fixed_world.py` — `to_game_setting()` now calls `normalize_world()`
  between numeric-code normalization and room-progression wiring; uses derived path
- ✅ 34 new tests in `tests/test_world_normalizer.py` — all repairs individually,
  combined scenarios, immutability, and parametrized loading of worlds 015–021
- ✅ 143/143 tests pass (3 pre-existing failures unchanged)

---

### Status
- **Current phase:** Phase 11 complete — the deterministic world normalizer repairs LLM-generated world JSON before the engine ever sees it. All 7 real worlds (015–021) now load and normalize cleanly. The derived solution_path correctly routes agents toward the actual win condition instead of the world-builder's hallucinated path. 143 tests pass.
- **Last updated:** 2026-06-08
- ⬜ Confirm local LLM serving approach → ✅ Ollama (`qwen2.5:7b`) confirmed working end-to-end.
