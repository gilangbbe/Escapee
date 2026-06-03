# Journal — Multi-LLM Agent Escape Room Simulation

> A running log of every decision made while building this project. Newest entries at top.

---

## 2026-06-02 — Migrated to fixed authored story format via compatibility adapter

Added support for the new canonical authored game format wrapped as
`{"world": ...}` (rooms as rich objects with adjacency/goal metadata, plus
objects/rules/solution/win), while keeping the deterministic simulator and
orchestrator intact.

**What changed:**
- New module: `app/schemas/fixed_world.py`.
  - `FixedWorldEnvelope` / `FixedWorld` / `FixedRoom` / `FixedObject` schemas.
  - `load_setting_compat(data)` accepts either:
    1) legacy runtime `GameSetting` dicts, or
    2) new fixed authored envelope (`{"world": ...}`).
  - Always returns validated runtime `GameSetting` for engine use.
- Adapter logic added to bridge format gaps:
  - Flattens room objects to runtime room ids.
  - Infers door `connects_to` from room adjacency when not explicitly provided
    on objects.
  - Normalizes symbolic numeric lock codes like `door_code_842` into executable
    keypad values (`842`) when `code_digits` indicates a numeric lock.
  - Injects a default 2-agent cooperative cast when authored stories omit
    persona definitions (keeps multi-agent runtime operational).
- Web integration:
  - `app/web/server.py` now uses `load_setting_compat(...)` in
    `current_setting()`, so one entry point serves both formats.

**Verification:**
- New tests: `tests/test_fixed_world_format.py`
  - fixed-world envelope converts correctly (rooms, win target, door link,
    normalized code, default personas),
  - legacy `GameSetting` still loads via same compatibility function.
- Full suite green: 97 passed.

**Design decision:**
- Runtime remains `GameSetting` + existing deterministic engine (low-risk).
- Fixed-world authored JSON is now a stable content contract, with a thin,
  deterministic adapter handling format differences.

---

## 2026-06-02 — Inference Control Layer: grammar-constrained structured generation

Reframed the looping/instability from a prompt problem to an **agentic control
loop** problem: a 7B model was being asked to be world-tracker + JSON compiler +
strategist + actor in one window. The state-tracking (candidate actions, progress
ledger, objective board) and the cognitive/blackboard layers were already
offloaded to deterministic code; the missing piece was the **inference control
layer** — we were still trusting the model to free-hand valid JSON via the loose
`format="json"` flag, which is exactly what lets it drop a comma, get creative
with an array, and either crash the turn or loop.

**Built:**
- `OllamaClient.chat` gained a `format_schema: dict | None` param. When set, it's
  passed straight to Ollama's `format` field — **native structured outputs**:
  Ollama constrains decoding so every sampled token conforms to the JSON Schema.
  This is grammar-based sampling (the Outlines/SGLang idea) **in-process**, with
  no extra dependency. `json_mode` stays as the looser fallback.
- `GamePlayerAgent.decide()` now passes `GameTurn.model_json_schema()` and
  `propose_plan()` passes `PlanProposal.model_json_schema()`, so both LLM
  boundaries in the play loop emit shape-valid objects by construction.
- **Token efficiency / prompt cleanup:** because the decoder now guarantees
  structure, the text JSON schema was deleted from the player prompt — the
  brace-heavy `AVAILABLE ACTIONS` JSON block and the `Expected JSON shape` example
  became a plain semantic verb→fields cheatsheet, and the plan prompt's JSON-shape
  lines were dropped. Bonus: this removed every `{{}}`-escaped literal from the
  `.format`-ed templates, killing the brace-escaping regression class for good.
- The GM blueprint path deliberately keeps json-mode + the validate/repair loop:
  `GameSetting` is large and gated by semantic `model_validator`s (referential
  integrity) that shape-only constrained decoding can't satisfy, so the repair
  loop still earns its keep there.

**Design note:** constrained decoding enforces *shape*, not *semantics* — a turn
is guaranteed to parse, but the engine remains the only authority on whether the
action is legal/useful. So this composes with (doesn't replace) the existing
validate/repair loop and the deterministic action-validation gate.

**Verification:** 90/90 backend tests green (updated the three v2 fake `chat`
doubles for the new kwarg and the persona-repin test for the cheatsheet form).

---

## 2026-06-10 — Phase 10: Shared objective board + collaborative plan + goal-level no-repeat

Phase 9 stopped the agents from re-running the *exact same* action in the *exact
same* world, but they still re-did things they had already **accomplished**: open
the locker on turn 10, then try to open it again on turn 18 because the world
fingerprint had drifted in between. The loop guard only caught identical-world
repeats, not *already-satisfied goals*. The agents were also each carrying their
own private (and diverging) idea of "the plan." This phase adds a deterministic,
ground-truth objective board and a single team-debated plan.

**Built — extensions to `app/cognition/team_cognition.py`:**
- **Objective board (`derive_board(state) → SolutionBoard`)** — recomputed from
  the authoritative `GameState` every turn. SOLVED lists opened/taken objects,
  learned info, restored power, and reached rooms; STILL-TO-DO lists the locked
  interactable objects (each with a **non-spoiler** `_requirement_hint`, e.g.
  "(needs a code)", "(needs the right tool used on it)") plus the WIN CONDITION
  until it's met. This board is the *antidote to memory drift* — the team trusts
  it over their own recollection.
- **Goal-level no-repeat (`already_done(player, action, state)`)** — derived from
  current state, not history: blocks TAKE of an already-taken item,
  ENTER_CODE/SET_FUSE on an already-open object, USE on an object whose effect is
  already applied, and MOVE to the room you're already in.
- **`blocked_reason(player, action, world_key, state)`** — single guard the
  orchestrator calls: free actions pass; otherwise it returns the goal-level
  "ALREADY DONE — pick the NEXT unsolved step" note, then falls back to the
  Phase 9 identical-world redundancy note.
- **Team plan (`TeamPlan` / `set_plan` / `plan.refresh(solved_ids)`)** — an
  ordered, ticked checklist stored once for the whole team; steps auto-tick when
  a solved object id appears in their text. The deterministic SOLVED/UNSOLVED
  board remains authoritative.

**Built — collaborative planning debate (multi-agent):**
- **`agent.propose_plan(state, log, draft_plan=None)`** + `PlanProposal` schema +
  `build_plan_system_prompt` / `build_plan_user_prompt`. Pass 1 the first agent
  *proposes* a plan from the objective + its private clues; each later agent
  *critiques and refines* the running draft with its own asymmetric knowledge.
- **`GameOrchestrator._plan_phase()`** runs this debate once, before round 1,
  stores the agreed plan in cognition, and announces it as a recorded
  `📋 Shared plan agreed by the team: …` SYSTEM event. Any LLM failure falls back
  to the running draft, then to `setting.solution_path`, so planning can never
  stall the game. Gated by `enable_planning` (off in scripted unit tests).

**Wiring:**
- `TeamBrief` gained `objective` / `solved` / `open_puzzles` / `plan_text`;
  `render()` now leads with an `=== OBJECTIVE BOARD (ground truth) ===` section
  (GOAL, ✅ SOLVED — never redo, 🔲 STILL TO DO — focus here, 📋 SHARED PLAN).
  `brief_for()` recomputes the board and refreshes the plan every turn.
- `_take_turn` now re-prompts on `blocked_reason(...)` (goal-level) instead of the
  raw `is_redundant(...)`, emitting `(loop avoided) …` and re-deciding up to
  `max_redecide` times.

**Verification:** 89 backend tests green (83 + 6 new: board, `already_done`,
`blocked_reason`, plan refresh/render, planning populates the plan, planning
falls back to `solution_path`). Live run (qwen2.5:7b) confirmed the `📋 Shared
plan agreed` debate at t0, a `✓ Progress!` milestone at t1, and repeated
`(loop avoided) BLOCKED …` guards stopping re-inspection of already-seen objects.

**Design notes:**
- The board is *derived*, never stored — it cannot drift out of sync with reality.
- Requirement hints are deliberately non-spoiler (never leak codes/answers).
- Planning is a lightweight 2-pass debate with a deterministic fallback to avoid
  7B deadlock; no circular import (`team_cognition` imports `TeamBrief`, not vice
  versa).

---

## 2026-06-09 — Phase 9: External cognition (memory, symbolic state, loop guard, progress, reflection)

The agents *understood* the puzzle but still looped — re-reading the log,
re-entering codes, re-trying unreachable rooms — because execution control lived
inside the LLM, which has no reliable memory of what it already tried. This phase
moves execution control **out of the model** into a deterministic cognition layer.
The LLM now only does what it's good at: reasoning, interpretation, hypothesis
generation. Memory, loop detection, progress tracking, and scheduling are code.

**Built — `app/cognition/team_cognition.py` (`TeamCognition`):**
- **Symbolic world fingerprint** — `world_fingerprint(state)` is a sha1 over the
  object states/locations, player locations/inventories, accessible rooms, power
  flags, and discovered info. Two situations with the same fingerprint are "the
  same world" — the basis for loop detection.
- **Episodic memory** — every action is logged as an `AttemptRecord`
  (turn, player, action signature, the world fingerprint *before* it ran,
  success, outcome).
- **Loop guard** — `is_redundant(player, action, world_key)` is true when this
  exact (player, action, world) tuple has already been attempted; the world has
  not changed, so the result is guaranteed identical. Free actions (say/look)
  are never blocked.
- **Progress ledger / intermediate rewards** — `compute_milestones(state)`
  yields tokens like `opened:supply_locker`, `took:access_card`,
  `reached:command_deck`, `learned:…`, `power:…`. New milestones are the reward
  signal that resets the stall counter.
- **Stall + reflection scheduling** — a stuck flag flips after
  `stall_threshold` no-progress turns; `should_reflect(turn)` fires every
  `reflect_every` turns to trigger a summarization checkpoint.
- **Curiosity** — surfaces visible-but-untried objects so the team explores
  instead of perseverating.
- **Summarized memory** — the agent's `reflection` text becomes the team summary,
  so long raw transcripts don't poison context.

**Wiring:**
- **`Observation.hint`** (`observation.py`) — a new grounded, non-spoiler field.
  The simulator now attaches hints to ambiguous failures (no code / wrong code,
  use-does-nothing, locked door, no route). Engine `message` text is unchanged
  (so substring asserts still pass); the orchestrator appends the hint only at
  the emit boundary — richer feedback for reasoning without leaking solutions.
- **`GameTurn.reflection`** + prompt rules — agents can fill a reflection during
  checkpoints; rules now say the system BLOCKS proven repeats, so never retry a
  "DO NOT REPEAT" action unless the world changed; stay curious; apply clues.
- **`TeamBrief.render()`** rebuilt to show: ⛔ blocked note, ⚠ stuck banner,
  🧠 reflection checkpoint, progress so far, summarized team memory, teammate
  proposals, DO-NOT-REPEAT list, and unexplored/reachable objects.
- **`GameOrchestrator`** now owns a `TeamCognition` (replacing the Phase 8
  ad-hoc `_hypotheses/_failed/_progress_signatures/_turns_since_progress/_stuck`
  blackboard). `_take_turn` computes the world fingerprint, builds the brief,
  and if the agent proposes a redundant action it **re-prompts** (up to
  `max_redecide=2`) with an explicit block note rather than wasting the turn.
  Milestones emit a `✓ Progress!` SYSTEM reward; the stall flag emits the
  re-evaluate prompt; reflection text is stored as summarized memory.

**Design notes:**
- The loop guard *re-asks* rather than *forces* — capped retries keep stubborn
  models (and scripted clients) from deadlocking; a harmless action still flows.
- Loop key is per-player `(player, signature, world_key)`, so asymmetric
  situations (different inventories/locations) aren't wrongly deduped.
- Hint stays a separate `Observation` field — never concatenated into the engine
  `message` — so the engine's text contract and existing tests stay intact.
- Milestones grow monotonically → a clean intermediate-reward signal without
  hand-tuned scores.

**Tests:** `test_phase4_orchestrator.py` migrated off the old internals onto
`orch.cognition.*`, plus new coverage: episodic memory of failures, redundant
action blocked + re-decided, progress milestone announced, stuck flag set/clears,
world-fingerprint changes only on real state change, reflection scheduled every N
turns. Full suite green (83 passed).

---

## 2026-06-02 — Phase 8: Multi-agent debate + adaptive (anti-loop) decision-making

The agents were correct but *stubborn* — when unsure they re-ran the same action.
This phase makes the team **deliberate**: share hypotheses, critique each other,
remember what failed, and change tactics when stuck, instead of looping.

**Built:**
- **`hypothesis` turn field** (`game_turn.py`) — an optional PUBLIC theory + the
  next step a player proposes. It's the unit of debate: teammates can support,
  critique, or counter it.
- **Team blackboard + stall detector** in `GameOrchestrator`:
  - `_hypotheses` — latest proposal per player (shown to teammates next turn).
  - `_failed` — team-wide map of failed action signature → why it failed.
  - `_progress_signatures` + `_turns_since_progress` — a novel *successful*
    action counts as progress and resets the counter; repeats/failures raise it.
  - `_stuck` flips true once `turns_since_progress >= stall_threshold`
    (default `max(3, players*2)`), emitting a one-shot SYSTEM "re-evaluate"
    prompt; it clears on the next real progress.
- **`TeamBrief`** (`game_builder.py`) — bundles teammate proposals, failed
  attempts, and the stuck flag, rendered into a new **TEAM DISCUSSION** section
  of each player's prompt. When stuck it prepends a "stop repeating, propose a
  DIFFERENT plan" banner.
- **Prompt rewrite** (`game_player_prompt.py`) — cleaned up the previously
  mangled rules block into 10 explicit collaboration rules: share what only you
  know, debate before big moves, divide the work, never retry a FAILED ATTEMPT
  without new evidence, act on what you learn, and re-evaluate when stuck.
- **Orchestrator wiring** — broadcasts each `hypothesis` as a public `(idea)`
  SPEECH event (so it appears in the story and in teammates' recent events), and
  threads the `TeamBrief` into `agent.decide()`.

**Design notes:**
- Debate is *encouraged*, not *blocking*. I deliberately did NOT gate actions
  behind a mandatory discussion round — with a 7B model that risks deadlock. The
  combination of (a) a visible blackboard, (b) failed-attempt memory, and (c) a
  stuck-triggered re-evaluation prompt produces the debate experience while the
  game always keeps moving.
- The stall heuristic keys on *novel successful* action signatures, so genuine
  progress (new code, item, room, first inspect) resets the timer, while
  re-inspecting or retrying a failure does not.

**Result:** live run against `qwen2.5:7b` shows real deliberation — players post
`(idea)` proposals ("Reading the captain's log will reveal the launch code…"),
respond to each other, and when they looped on an unreachable nav console the
**"team seems stuck — re-evaluate"** prompt fired at t10 and nudged a new plan.
79/79 tests pass (4 new: hypothesis broadcast, failed-attempt memory, stuck flag
set, stuck flag cleared on progress).

---

## 2026-06-01 — Phase 7: GM Narrator (the game becomes a watchable story)

Added the missing half of the Overview vision: a second LLM acting as the **Game
Master / storyteller**. After each player turn, it converts the structured action
+ the simulator's *authoritative* outcome into one or two sentences of immersive,
present-tense prose, so a human can simply read the unfolding story instead of
parsing `OBS`/`SAY` log lines.

**Built:**
- `backend/app/agents/gm_narrator_prompt.py` — narrator system prompt (narrate
  ONLY ground truth, never invent objects/exits/success, no JSON, no raw ids) +
  `describe_action()` (turns each of the 9 `GameActionType` verbs into plain
  language) + opening/turn/ending user-prompt builders + `_humanize()` for ids.
- `backend/app/agents/gm_narrator.py` — `GameMasterNarrator` dataclass wrapping an
  `OllamaClient`, with a rolling `recent_window` memory for continuity and a
  **deterministic fallback** on every method so an LLM hiccup never stalls the game.
- New `EventKind.NARRATION` channel.

**Key architectural decision — narration is presentation-only:**
NARRATION events are streamed to observers via the `on_event` sink but **never
added to the shared `MessageLog`** (a new `record: bool` flag on
`GameOrchestrator._emit`). This preserves the two invariants that make the sim
trustworthy: (a) **anti-hallucination** — the narrator cannot corrupt ground
truth; and (b) **asymmetric information** — player agents read the `MessageLog`
to build context, so prose (which may describe another player's *private*
observation) must never enter it. Verified by a new test
(`test_narration_streams_but_never_enters_message_log`): narration reaches the
stream (opening + per-turn + ending) but `EventKind.NARRATION` is absent from
`orch.log.events`.

**Wiring (engine → runner → server → CLI → UI):**
- `GameOrchestrator` takes an optional `narrator`; emits opening/turn/ending
  narration around the existing structured events.
- `runner.build_narrator()` + `GameRunner(narrator=…)`.
- WebSocket `?narrate=true|false` query param (default on).
- `live_run.py` prints prose prominently and gained a `--no-narrate` flag.
- Frontend: `EventKind` adds `"narration"`; `NarrativeFeed` renders prose as a
  distinct italic block (📖); `styles.css` `.narration`; App has a **narrate**
  toggle. Feed heading renamed "Story".

**Result:** live run against `qwen2.5:7b` streams a continuous story — e.g.
*"Kade Rourke's fingers trace over the cold metal of the captain's log… 'Sealed
our gear behind the old launch code — zero-four-five-one.'"* 75/75 tests pass.
(Known nit: the storyteller occasionally embellishes a beat ahead of the literal
outcome; since it's presentation-only this can't affect the game — left as a
future prompt-tightening item.)

---

## 2026-05-31 — Phase 6: Live Playthrough + 7B Prompt/Engine Tuning (🎉 first live win)

Ran the agents for real against a local Ollama `qwen2.5:7b` via a new headless
streamer and tuned away the failures I actually observed. The deterministic engine
already guaranteed *correctness*; this phase was about getting a small local model
to reliably *make progress*.

**Built:**
- `backend/scripts/live_run.py` — headless CLI that drives the real
  `GamePlayerAgent`s through the `GameRunner` and prints the streamed narrative +
  final verdict. `../.venv/bin/python -m scripts.live_run --model qwen2.5:7b --rounds 30`.

**Observed failures → fixes (each verified with another live run):**
1. **Bracket-id copying.** The model echoed the `[id]` display form verbatim
   (`target_id":"[captains_log]"`) → "There is no '[captains_log]'". Fixed at two
   levels: (a) a `GameAction` `model_validator` (`_normalize_ids`) that strips
   surrounding `[]`/whitespace from `target_id`/`item_id`/`to_room`/`to_player_id`;
   (b) the player prompt now says brackets are *display-only* — write the bare id.
2. **Re-inspect loops.** Agents kept re-reading the same object instead of acting
   on what they learned. Added a per-agent **"ACTIONS YOU ALREADY TOOK"** section
   (`GamePlayerAgent._own_actions`, last 6) + a "don't repeat — act on what you
   learned" nudge, plus rules to explore through open exits.
3. **Clue treated as a held item.** A player tried to `use` its override-phrase
   clue as if holding it. Clarified: a clue is *text*, submit it with `enter_code`
   (`"code"`), never `item_id`. Dropped temperature 0.7 → 0.4 for steadier JSON.
4. **Design bug — `captains_log` was `takeable: True`.** A player *took* the log,
   removing it from the room so no one could read the code afterward. The log is a
   read-only info object — set `takeable: False`.

**Result:** with all four fixes, run 6 was a clean **cooperative escape in 30 turns**:
player_1 read the log → entered 0451 → took the access card → opened the command
door → moved → took the power cell → slotted it; player_2 moved through the now-open
door and entered its private override phrase on the nav console to reveal the cell.
Both asymmetric clues used, both rooms traversed, full puzzle chain solved.

**Decisions:**
- Default model is now `qwen2.5:7b` (the available local 7B; `llama3` was never pulled).
- Anti-hallucination held up: every invalid id/action the model produced was caught
  by the engine and returned as a grounded observation rather than corrupting state.
- 74/74 backend tests green (added a bracket-normalization test); frontend still builds.

---

## 2026-05-30 — Phase 5: Web App (FastAPI WebSocket + React/Vite UI)

**Built (backend web layer):**
- `backend/app/web/serializers.py` — JSON serializers for the observer UI:
  `event_to_dict`, `state_snapshot` (grounded full-world view from `GameState`,
  enums rendered to strings), `setup_message` (scenario + personas), `result_to_dict`.
- `backend/app/web/runner.py` — `GameRunner` + `build_agents`: constructs one
  `GamePlayerAgent` per persona (each bound to a local Ollama model) and streams
  setup → state → events (+ a fresh snapshot after each) → result to an async `send`.
- `backend/app/web/server.py` — FastAPI app: `GET /`, `GET /api/setting`, and
  `WS /ws/game?model=&rounds=`. CORS open to the Vite dev origins. Runtime errors
  (e.g. Ollama unreachable) are surfaced as an `{type:"error"}` message.
- `backend/app/main.py` — ASGI entrypoint (`uvicorn app.main:app`).
- `backend/tests/test_phase5_web.py` — 8 tests: serializer shapes/grounding,
  `GameRunner` streaming a full scripted win, and the WebSocket endpoint driven to
  victory with scripted agents (via monkeypatched `build_agents`) + the REST setting
  endpoint. **Suite now 73/73 green.**

**Built (frontend — React + Vite + TypeScript):**
- `frontend/` scaffold: `package.json`, `vite.config.ts`, `tsconfig*.json`, `index.html`.
- `src/types.ts` — TS mirrors of the backend message shapes.
- `src/useGameSocket.ts` — WebSocket hook accumulating setup/event/state/result.
- `src/components/` — `SetupPanel` (scenario + crew), `NarrativeFeed` (live timeline
  with speech/observation/system styling + private badges), `StatePanel` (grounded
  world snapshot: players, objects by state, power, win condition).
- `src/App.tsx` + `styles.css` — three-column terminal-style text UI with model/rounds
  controls and a start/stop + status badge. `npm run build` passes (type-check + bundle).
- `frontend/README.md` — run/build instructions.

**Key decisions:**
1. **Observer/spectator UI:** the frontend watches the whole game (sees all events incl.
   private-tagged ones) — it's a debugging/visualization surface, not a player. Privacy
   in the *engine* is still enforced (agents only get their own clues); the UI just labels
   private observations.
2. **Push a state snapshot after every event** so the panel always reflects authoritative
   ground truth — never the models' claims.
3. **Streaming decoupled via the orchestrator's `on_event` hook**; the WebSocket sender is
   just another sink. The same `GameRunner` is unit-tested with a fake `send`, and the WS
   endpoint is tested with scripted agents (no Ollama needed in CI).
4. **Text-only** per the Overview requirement — no canvas/graphics, pure narrative + state.

**Status:** All five planned phases are functionally complete. Live runs require a local
Ollama server with a model pulled (e.g. `ollama pull llama3`); offline everything is
exercised by 73 passing tests.

**Next (Phase 6 polish):** end-to-end live run against Ollama, prompt tuning for 7B
reliability, and final docs.

## 2026-05-30 — v2 player agents migration + Phase 4: Orchestration Loop

**Built (v2 agent path, non-destructive — v1 agent files kept):**
- `backend/app/agents/game_turn.py` — `GameTurn` ReAct schema wrapping the v2 `GameAction`.
- `backend/app/agents/game_player_prompt.py` — 7B-friendly player prompt for the v2
  world: persona re-pinned every turn + the GameAction grammar
  (look/inspect/take/enter_code/use/set_fuse/move/give/say) + embedded JSON schema.
- `backend/app/context/game_builder.py` — `GameStateView` + `build_game_state_view()`
  derived from authoritative `GameState`; `seed_private_clues()` loads asymmetric
  clues from the setting. Reuses the deterministic `render_recent_events` /
  `summarize_events` from the v1 context module.
- `backend/app/agents/game_player_agent.py` — `GamePlayerAgent.decide()`: assemble
  grounded context → Ollama (json mode) → parse/repair into a validated `GameTurn`,
  with per-player rolling-summary memory.

**Built (Phase 4 — Orchestration Loop):**
- `backend/app/orchestrator/loop.py` — `GameOrchestrator`: owns the `GameSimulator`
  (ground truth), the shared `MessageLog`, and the list of `GamePlayerAgent`s.
  `run()` walks players round-robin and runs the full ReAct cycle each turn
  (Thought→Speak→Action→Observation): records public `speak` as SPEECH, applies the
  validated `GameAction` to the simulator, records the grounded `Observation`
  (public broadcast vs private-to-actor), streams every event to an optional async
  `on_event` sink (the Phase 5 WebSocket hook), and ends on win or turn limit.
  Returns a `GameResult` (won / turns / reason / events).
- `backend/tests/test_phase4_orchestrator.py` — 12 tests: v2 state-view grounding,
  persona re-pinning, ReAct parse, agent message assembly w/ private clue, a scripted
  full cooperative win through the orchestrator, clue seeding + privacy, turn-limit
  stop, unknown-persona rejection. **Suite now 66/66 green.**

**Key decisions:**
1. **Round-robin scheduling** in the orchestrator (agents act in supplied order). The
   simulator remains the only mutator of state; agents influence the world only via
   validated actions. Simple and fully deterministic given scripted/seeded LLM output.
2. **Streaming via an injectable async `on_event` sink** decouples the loop from the
   transport. Phase 5's WebSocket layer just plugs in here; tests pass a list-appender.
3. **`MessageLog` + the deterministic summarizer are shared** between v1 and v2 — the
   only v2-specific context piece is the `GameStateView` built from `GameState`.
4. **Private observations routed by `audience_id`**, so a failed/secret result is fed
   back only to the acting player; public results broadcast to all (asymmetric info
   preserved through the live loop).

**Next:** Phase 5 — FastAPI WebSocket endpoints streaming the `on_event` feed + a
React/Vite text UI (narrative feed + per-player state panel).

## 2026-05-30 — GameSetting v2 schema migration

**Why:** The user provided a new authoritative `game_setting` JSON (a chemistry-lab
example) that will be the actual schema the GM LLM generates. It replaces the
v1 rooms/objects/items/locks/puzzles model with a flatter, more expressive
**object + state + requirement** world model. Migrated the architecture to it.

**Built (v2, non-destructive — v1 code kept intact and still green):**
- `backend/app/game/game_setting.json` — the user-provided reference schema instance.
- `backend/app/schemas/game_setting.py` — `GameSetting`, `GameObject`, `WinCondition`,
  `PlayerPersona`, `PlayerClue`, `ObjectState` enum + `OPEN_STATES`. Faithful
  requirement fields (`requires_code`/`code_digits`/`requires_tool`/`requires_liquid`/
  `requires_power`/`fuses`/`contains_info`) plus optional engine hints (`reveals`,
  `liquid_property`, `connects_to`, `provides_power`) and optional extensions
  (`start_room`, `players`, `player_clues`). `model_validator` enforces referential
  integrity, fuse values, containment-cycle freedom, win target, and clue refs.
- `backend/app/game/orbital_decay_setting.py` — Orbital Decay ported to v2 with 2
  players and asymmetric `player_clues` to preserve cooperative play.
- `backend/app/engine/game_state.py` — `GameState` runtime: location-chain resolution,
  visibility, container/open logic, fuse→power-flag recompute, auto-unlock of
  `requires_power` consumers.
- `backend/app/engine/game_actions.py` — `GameAction` + `GameActionType`
  (look/inspect/take/enter_code/use/set_fuse/move/give/say).
- `backend/app/engine/game_simulator.py` — `GameSimulator.step()` with shared
  open/reveal/power resolution and win checking. Reuses the v1 `Observation` model.
- `backend/tests/test_game_setting.py` (7) + `backend/tests/test_game_simulator.py` (10),
  incl. a full cooperative playthrough to victory. **Suite now 54/54 green.**

**Accommodated existing architecture to v2:**
- `backend/app/agents/gm_prompt.py` — GM system prompt rewritten to describe the v2
  object/state/requirement world model; `gm_json_schema()` now emits
  `GameSetting.model_json_schema()`.
- `backend/app/llm/ollama_client.py` — `generate_blueprint()` now validates against
  `GameSetting`.

**Key decisions:**
1. **Non-destructive migration:** kept v1 schema/engine/agents intact (all tests still
   pass) while v2 becomes the forward path. Reduces risk and preserves a working baseline.
2. **`Observation` and `json_repair` are shared** between v1 and v2 — single tested
   boundary for grounded feedback and LLM JSON validation/repair.
3. **Optional player extensions on `GameSetting`:** the raw generated schema (chemistry
   JSON) has no players, so `players`/`player_clues`/`start_room` are optional; authored
   games (Orbital Decay v2) add them for asymmetric multiplayer.
4. **Engine hints kept separate from the faithful schema fields** so generated settings
   parse as-is, while authored settings can add `reveals`/`connects_to`/`provides_power`.

**Next:** migrate the player agent/context path to v2 (or add a v2 agent path), then
Phase 4 (asyncio orchestration loop) and Phase 5 (FastAPI WebSocket + React app).

## 2026-05-29 — Phase 3: Player Agents, ReAct loop & Context Manager

**Built:**
- `backend/app/agents/player_turn.py` — `PlayerTurn` schema: one JSON object per turn `{thought, speak, action}` wrapping a validated `PlayerAction`.
- `backend/app/agents/player_prompt.py` — 7B-friendly player system prompt (persona + skills + action grammar + JSON schema re-pinned every turn) and the per-turn user prompt assembler.
- `backend/app/context/channels.py` — `MessageLog` + `Event` timeline with public/private routing and per-player private clues.
- `backend/app/context/builder.py` — `StateView` (grounded per-player snapshot from `WorldState`), recent-window splitter, and a deterministic bounded rolling summary (no LLM).
- `backend/app/agents/player_agent.py` — `PlayerAgent.decide()`: assemble context → call Ollama (json mode) → parse/repair into a valid `PlayerTurn`, with per-player rolling-summary memory.
- `backend/tests/test_phase3_agents.py` — 14 tests (state view grounding, channel privacy, window/summary bounds, ReAct parsing, prompt pinning, fake-LLM agent + repair). Suite now 37/37.
- Added `pytest-asyncio` + `pytest.ini` (`asyncio_mode=auto`).

**Key decisions:**
1. **Re-pin persona + objective + action schema EVERY turn** in the system prompt; rebuild the grounded state view each turn from authoritative `WorldState`. This is the anti-forgetting mechanism for small models.
2. **Bracketed [id] discipline:** the state view exposes entity ids; the prompt forbids inventing ids. Combined with the engine rejecting unknown entities, this closes the hallucination loop.
3. **Deterministic rolling summary** (bounded to 30 bullets) instead of an LLM summarizer for now — keeps the whole context pipeline testable offline. Swappable later.
4. **Privacy by construction:** private observations/clues are routed via `audience_id`; `visible_to()` enforces what each player can see, supporting asymmetric information.
5. **PlayerAgent reuses the same JSON validate/repair loop** as the GM — single, tested code path for all LLM boundaries.

**Next:** Phase 4 — the asyncio orchestration loop: turn scheduling across agents, applying actions to the simulator, writing observations/speech to the `MessageLog`, broadcasting, and detecting win/lose end states.

## 2026-05-29 — Canonical game + Phase 2: World Simulator & State Machine

**Built:**
- `backend/app/game/orbital_decay.py` — "Orbital Decay", a hand-authored (not LLM-generated) 3-room cooperative escape room. Validates against the Phase 1 schema. Solvable only with BOTH players' asymmetric clues.
- `backend/app/engine/actions.py` — `PlayerAction` Pydantic schema (the validated action boundary).
- `backend/app/engine/observation.py` — `Observation` model (grounded feedback + room view).
- `backend/app/engine/state.py` — `WorldState` runtime ground truth (player locations/inventories, lock states, hidden flags, per-item `ItemLocation` tracking, world flags, progress).
- `backend/app/engine/simulator.py` — `WorldSimulator.step()`: deterministic handlers for look/inspect/move/take/give/unlock(key|code|sequence)/solve/say, plus win evaluation.
- `backend/tests/test_simulator.py` — 13 tests incl. a full cooperative playthrough to victory. Suite now 23/23 green.

**Key decisions:**
1. **Authored the game myself** (per request) instead of invoking the GM LLM — gives a known-good, deterministic target for engine development and tests.
2. **Hidden items must pre-exist at a real location** (room/container) with `hidden=True`; a `reveal_item` puzzle just flips visibility. This avoids the "where does a revealed item spawn?" ambiguity and keeps referential integrity intact.
3. **Locks vs puzzles cleanly separated:** locks are opened directly via `unlock` (key=inventory, code=value, sequence=ordered ids + required items held); puzzles mutate the world via `solve` (reveal/unlock/flag).
4. **Observations derive ONLY from ground truth** — never from model claims. Invalid actions return grounded failure messages, so a 7B model self-corrects each turn.
5. **Determinism:** zero LLM calls in the engine; same state + action always yields the same observation. Verified by the playthrough test.

**Next:** Phase 3 — Player agent system prompts, ReAct loop wiring to `PlayerAction`, and the context manager (rolling summary + per-turn state view + public/private channels).

## 2026-05-29 — Phase 1: GM Blueprint Schema & System Prompt

**Built:**
- `backend/app/schemas/blueprint.py` — Pydantic v2 models: `Item`, `Lock`, `GameObject`, `Exit`, `Room`, `Puzzle`, `PlayerClue`, `PlayerPersona`, `WinCondition`, and root `RoomBlueprint`.
- `backend/app/agents/gm_prompt.py` — GM system prompt with hard rules, plus auto-generated JSON Schema embedding.
- `backend/app/llm/json_repair.py` — robust JSON extraction (code fences/prose), validation, and structured repair instructions.
- `backend/app/llm/ollama_client.py` — async Ollama client + `generate_blueprint` retry/repair loop (`format=json`).
- `backend/app/schemas/sample_blueprint.py` — hand-authored 'Forgotten Lighthouse' reference room.
- `backend/tests/test_blueprint.py` — 11 tests (schema validity, integrity rejections, JSON extraction, repair loop). All pass.

**Key decisions:**
1. **Referential integrity validator** on `RoomBlueprint` is the anti-hallucination linchpin: every id reference (exits, locks, key items, container contents, puzzle rewards, clues, win conditions) must resolve, or the blueprint is rejected before reaching the engine.
2. Lock-type-specific validators force the GM to supply a real solution for each lock kind (key/code/sequence) — no unsolvable rooms.
3. Repair loop is **LLM-agnostic and unit-tested** without a running model; Ollama only needed for live generation.
4. Bootstrapped pip via `ensurepip` (the `.venv` shipped without pip) and installed deps.

**Next:** Phase 2 — deterministic World Simulator / State Machine that loads a validated blueprint and exposes action handlers returning grounded Observations.

## 2026-05-29 — Project kickoff & foundation

**Context:** Read `Overview.md`. Goal is a web-facing, text-only narrative escape-room game driven by local 7B LLMs — a GM that generates rooms and Player agents that collaborate via a ReAct loop.

**Decisions:**
1. **Ground truth lives outside the LLM.** A deterministic Python state machine (Pydantic-backed) owns all world state. LLMs only *propose* actions; the engine validates and applies them. This is the primary anti-hallucination guardrail.
2. **Strict JSON schemas at every LLM boundary** (GM blueprint output + Player action output), with a reject-and-repair retry loop.
3. **Context strategy for 7B models:** re-pin persona + objective + win conditions every turn; inject a fresh structured state view; keep recent turns verbatim; compress older history into a rolling summary to avoid context overflow.
4. **Stack:** Ollama (local 7B serving) · FastAPI + Asyncio (backend) · Pydantic v2 (validation) · React + Vite + TypeScript (frontend) · WebSocket (realtime).
5. **Orchestration:** start with raw asyncio turn loop for transparency; consider LangGraph later.
6. **Environment:** use the project-local `.venv` (Python 3.11.9), verified working.

**Done:**
- Verified `.venv` is valid.
- Authored `ProjectDocument.md`, `Roadmap.md`, `Journal.md`.

**Next:** Await user's choice of which phase to begin (recommend Phase 1: GM Blueprint Schema).
