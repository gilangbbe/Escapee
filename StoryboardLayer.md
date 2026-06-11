# Storyboard Layer — Source of Truth

> This document defines the architecture, schema, and integration plan for the
> Storyboard system. Update it whenever decisions change. Read it before
> touching any storyboard-related code.

---

## Why This Exists

The game has three layers:

| Layer | What it does | Who writes it |
|-------|-------------|---------------|
| 1 — World JSON | Mechanical puzzle: rooms, objects, locks, codes | AI (Layer 1 generator) or hand-crafted |
| **1.5 — Storyboard** | **Narrative layer: plot, personas, scripted beats** | **StoryboardGenerator (this layer)** |
| 2 — Game Runtime | Plays the game; narrator translates actions to prose | Orchestrator + Narrator LLM |

**The problem this solves:** World JSONs contain only mechanical data. The narrator LLM at runtime defaults to generic "gothic manor / eerie / whispers secrets" prose because it has no specific story to tell. The storyboard pre-generates that specific story — once, offline — so the runtime narrator retrieves facts and scripted moments instead of improvising them.

**The principle:** Expensive creative work belongs pre-game, not real-time. Runtime LLM calls should fill gaps between fixed anchor points, not generate the most important story moments under time pressure.

---

## Key Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| One storyboard per world or per session? | **One per world** | Consistency across replays. Variety comes from player behavior, not re-rolled story. |
| Generated when? | **Auto on first load + CLI for manual/testing** | Transparent to players; developer can inspect and regenerate. |
| Stored where? | `backend/app/game/world_XXX_storyboard.json` | Lives next to the world JSON it describes. |
| Stale detection | **mtime check**: if `world_XXX.json` is newer than storyboard, regenerate | World changes should trigger a fresh story. |
| Storyboard generation model | `qwen2.5:14b` (~8.7GB Q4_K_M), temperature 0.85 | Best JSON discipline + creative richness within 16GB M5. Runs once offline, 60s is acceptable. |
| Runtime narrator/dialogue model | `qwen2.5:7b` (keep current), temperature 0.8 | Speed is what matters at runtime — storyboard does the heavy creative work. |
| Model configuration | Separate `OllamaClient` instances per role, model names via env vars | Swap models without touching code. `STORYBOARD_MODEL=qwen2.5:14b`, `NARRATOR_MODEL=qwen2.5:7b`. |
| Runtime persona injection | Adapted persona description replaces raw `role` string in ALL dialogue calls | Solves the "Systems Operator says power surge in a murder mystery" contamination problem. |

---

## The Persona Problem (Why Adaptation Matters)

Current personas have **fixed professional labels** with strong genre associations baked into model training:

- `Systems Operator` → model fires: electrical, power routing, circuits
- `Field Analyst` → forensics, evidence collection
- `Scout` → physical recon, movement

In a Victorian manor murder mystery, a Systems Operator saying "power surge" breaks immersion. In an asylum horror, a Field Analyst sounds like a CDC inspector. The fix is NOT to remove personas. The fix is the storyboard generates a **world adaptation** for each persona — the runtime narrator and dialogue models never see the raw label, only the adapted version.

### Adaptation examples

| Persona | Raw label | Manor mystery adaptation | Asylum horror adaptation |
|---------|-----------|--------------------------|--------------------------|
| Riley Sato | Systems Operator | "Reads patterns in human behavior — timing, sequence, what the evidence implies about who was where when" | "Studied institutional protocols before the incident — schedules, patient rotations, key access logs" |
| Alex Quinn | Field Analyst | "Reads crime scenes like documents — a displaced object is a sentence, a footprint is a paragraph" | "Documents observable conditions methodically — what each room's state tells you about who was last there" |
| Mara Vance | Scout | "Reads physical spaces on instinct — where dust was disturbed, which door was used recently, what the floor reveals" | "Navigates dangerous spaces by feel — knows which hallway sounds wrong, which door opens too easily" |

The persona name and backstory stay unchanged. Only the `world_role` field is generated per world.

---

## Storyboard JSON Schema

File: `backend/app/game/world_XXX_storyboard.json`

```json
{
  "world_id": "world_033",
  "generated_at": "2026-06-10T09:00:00",
  "schema_version": "1",

  "plot": {
    "victim": "Eliza Harwick, 52, was found face-down in the study at midnight, her reading glasses still on.",
    "threat": "One of the guests in this manor killed her and has not left the building.",
    "stakes": "If the team runs out of time, the killer destroys the last evidence and walks free tonight.",
    "timeline": "Three weeks ago, Eliza discovered a forged deed hidden in the library. She wrote letters to each guest demanding they return. She was dead before any of them arrived.",
    "tension_hint": "Whoever killed her knew exactly which room she would be in and when.",
    "atmosphere": "The floorboards are still warm from a fire that went out hours ago. Every door in the manor is slightly ajar, as if someone left in a hurry.",
    "protagonist_context": "These guests were summoned by Eliza herself. Now she is dead and the roads are flooded."
  },

  "adapted_personas": {
    "Alex Quinn": {
      "world_role": "Alex reads crime scenes like documents — a displaced object is a sentence, a ring mark on a frame is a name.",
      "voice": "Speaks in precise observations, never speculation. States what the evidence is, not what it means.",
      "vocabulary": [
        "the mark on the frame is made by a ring, not a key",
        "this was moved after the fact",
        "the sequence doesn't match what we were told",
        "someone came back here",
        "look at where the dust isn't"
      ]
    },
    "Riley Sato": {
      "world_role": "Riley tracks patterns in human behavior — timing, access, the logic of who could have been where.",
      "voice": "Methodical and quiet. Thinks out loud in sequences. Connects dots others miss.",
      "vocabulary": [
        "the timing doesn't add up",
        "only one person had access to both rooms",
        "this was planned in advance",
        "the pattern here is the same as the other room",
        "whoever did this knew the layout"
      ]
    },
    "Mara Vance": {
      "world_role": "Mara reads physical spaces on instinct — where dust was disturbed, which door was used recently, what the floor tells her.",
      "voice": "Sparse, physical. Trusts her gut over logic. Warns before she explains.",
      "vocabulary": [
        "don't touch it yet",
        "someone was standing here recently",
        "this door wasn't locked from this side",
        "something's wrong here, I can feel it",
        "the dust moved"
      ]
    }
  },

  "room_stories": {
    "entry_hall": "Eliza met each arriving guest here. The last person she greeted never signed the visitor log.",
    "parlor_room": "The guests gathered here the night of the murder. The candelabra was still lit when the body was found.",
    "study_corridor": "This is where Eliza was working the night she died. The bloodstains are still visible under the rug.",
    "library_chamber": "The forged deed Eliza discovered is hidden somewhere in this room. So is the letter she never sent.",
    "attic_stairwell": "The killer came through here to avoid the main hall. The mask was left behind."
  },

  "discovery_beats": {
    "faded_scarf": "The scarf still holds the knot Eliza tied herself — but someone retied it afterwards, tighter.",
    "hidden_key_locket": "The locket belonged to Eliza. The photo inside has been removed, but the outline of a face remains.",
    "hidden_key": "The key was hidden under the bloodstained floorboard — not lost, deliberately concealed after the murder.",
    "diary_of_guest": "The diary is Eliza's. The last entry is dated the night of the storm. The handwriting changes halfway through the final page.",
    "murderer_mask": "The mask is identical to the one in the portrait of the manor's last heir. It was not left here by accident."
  },

  "phase_guidance": {
    "establishing": "Ground the reader in the physical world. Name the victim early — Eliza is real, she was here. Build dread through specific sensory detail, not generic atmosphere.",
    "investigating": "Connect objects to people. Every discovery should narrow something down. The narrator should make the reader feel the picture forming.",
    "converging": "The pieces are almost assembled. The narrator feels the clock. Short sentences. Specific observations. The killer is in this building.",
    "revealing": "One thing is now certain. The narrator does not undersell it. This moment is what the story has been building toward."
  },

  "conversation_seeds": {
    "Alex Quinn": [
      "The scarf knot was retied after Eliza took it off — someone handled it.",
      "Two guests are unaccounted for during the estimated time of death.",
      "The diary entry stops mid-sentence. She was interrupted."
    ],
    "Riley Sato": [
      "The guest log shows someone arrived an hour before the others — and didn't sign out.",
      "The layout of the manor means you can reach the study from the attic without crossing the main hall.",
      "Eliza's letter said midnight. Whoever killed her knew that before they arrived."
    ],
    "Mara Vance": [
      "The attic door opens from the inside — it was used recently.",
      "There are two sets of footprints in the study corridor dust. One of them stops and turns back.",
      "The candle in the parlor burned down unevenly. Someone moved it."
    ]
  },

  "ending_guidance": {
    "won": "The team did not just escape — they exposed what Eliza died trying to reveal. The ending should feel like her letter finally reached its destination.",
    "lost": "The killer walks free. The forged deed stands. The ending should feel like the manor closes back around its secret.",
    "lost_by_wrong_deduction": "The team had every piece of evidence. The answer was in front of them. The killer watched them name the wrong person and slipped out through the attic."
  },

  "solution": {
    "question": "Who murdered Eliza Harwick?",
    "answer": "Lord Pemberton",
    "answer_aliases": ["pemberton", "lord pemberton", "james pemberton", "the lord", "james"],
    "answer_type": "person",
    "motive": "He forged the deed to inherit the estate. Eliza discovered it and summoned him to confess before the others arrived.",
    "proof_object": "diary_of_guest",
    "proof_sentence": "The diary's final page is not in Eliza's handwriting. He wrote it himself to mislead whoever found it first.",
    "hint_1": "The evidence points to someone who knew Eliza's exact schedule and had access to the study before the other guests arrived.",
    "hint_2": "Go back to the diary. The handwriting on the final page is not Eliza's. Only one guest was already in the manor when she was killed.",
    "victory_narration_hook": "The team named the killer. The mask, the diary, the unsigned log entry — it all pointed to one person. {player_answer}.",
    "defeat_narration_hook": "They had every piece. The answer was assembled and waiting. They ran out of time to see it."
  }
}
```

---

## Human Deduction Phase

### Design Philosophy

The AI agents do the legwork — searching rooms, picking up objects, unlocking doors. The human player makes the final call. This is the classic mystery structure: investigators surface the evidence, the detective names the killer.

This feature is only possible because the storyboard pre-generates a coherent mystery with a real answer. Without a storyboard, asking the human to deduce is arbitrary — there is no actual story to reason from. With a storyboard, every discovery beat, every conversation seed, every room story was building toward this moment.

The human is not a spectator who occasionally types commands. They are the detective. The AI team hands them the complete body of evidence and says: *your call*.

---

### The `solution` Section (Sealed)

The storyboard contains a `solution` block that is **never shown to the narrator and never logged**. It exists only in the orchestrator's validation logic. The narrator does not know the answer — it only knows the `tension_hint` and story atmosphere. This keeps the game honest.

The solution is genre-agnostic:

| Field | Purpose |
|-------|---------|
| `question` | The exact question shown to the human player |
| `answer` | The canonical correct answer (normalized for matching) |
| `answer_aliases` | All acceptable phrasings (fuzzy match list) |
| `answer_type` | `"person"`, `"location"`, `"object"`, `"code"` — for UI rendering |
| `motive` | The full explanation (shown only after win — never before) |
| `proof_object` | The single most important evidence object |
| `proof_sentence` | One sentence that proves the answer — used in victory narration |
| `hint_1` | Vague directional hint (given after first wrong answer) |
| `hint_2` | Specific but non-spoiling hint (given after second wrong answer) |
| `victory_narration_hook` | Template the narrator uses to write the winning ending |
| `defeat_narration_hook` | Template for losing after exhausting all attempts |

Answer types by genre example:
- Murder mystery → `"person"`: "Who killed Eliza Harwick?"
- Sci-fi → `"object"`: "Which system was sabotaged?"
- Asylum horror → `"person"`: "Which doctor falsified the patient records?"
- Heist → `"location"`: "Where is the override panel hidden?"

---

### Trigger Condition

The deduction phase activates when **the escape item is in any agent's inventory AND the escape target object is accessible**. This means:

1. The full mechanical puzzle chain is complete
2. The team is physically capable of winning right now
3. But the final action is paused — handed to the human

The orchestrator checks this condition at the start of every AI turn. When it fires:
- AI turns stop
- A `HUMAN_DEDUCTION` event is emitted to the frontend
- The orchestrator enters `deduction_phase = True` and waits for human input

The trigger fires only once per game. Once the human submits (correct or exhausted), the game proceeds to conclusion regardless.

---

### Answer Format

The human submits two fields:

| Field | Required | Purpose |
|-------|----------|---------|
| Answer | Yes | The specific name/thing being accused — this is validated |
| Reasoning | No | "How do you know?" — used by narrator in victory ending, not validated |

The reasoning field matters for the story even though it is not validated. If the human says *"because the handwriting in the diary changed"*, the narrator can reference that specific observation in the ending narration. If left blank, the narrator uses the generic `victory_narration_hook`.

---

### Validation Logic

```
normalize(text):
  lowercase → strip punctuation → strip whitespace → collapse spaces

match(human_answer, solution):
  normalized = normalize(human_answer)
  canonical = normalize(solution.answer)
  aliases = [normalize(a) for a in solution.answer_aliases]

  if normalized == canonical: CORRECT
  if normalized in aliases: CORRECT
  if any(normalized in alias or alias in normalized for alias in aliases): CORRECT
  else: WRONG
```

Substring matching handles "Lord Pemberton" vs "Pemberton" vs "pemberton" vs "the lord". The alias list in the storyboard covers expected variations — the generator is instructed to include first name, last name, full title, and informal forms.

---

### Failure Handling — Three Attempts

```
Attempt 1 — wrong:
  → narrator emits hint_1 as a NARRATION card
  → human input reactivates (attempt counter shown: "2 attempts remaining")

Attempt 2 — wrong:
  → narrator emits hint_2 as a NARRATION card
  → human input reactivates (attempt counter shown: "1 attempt remaining")

Attempt 3 — wrong:
  → game ends in DEFEAT
  → narrator uses ending_guidance.lost_by_wrong_deduction
  → motive is revealed in the ending narration (post-game, like a mystery novel's final chapter)
  → the final mechanical action never executes — the story ends with the killer escaping

Attempt N — correct (any attempt):
  → AI executes the final mechanical action (confirmation beat — mask fits the panel)
  → narrator uses ending_guidance.won + victory_narration_hook
  → motive is revealed in the ending narration
  → if reasoning was provided: narrator weaves it into the ending
```

The three-attempt structure creates real stakes without being punishing. The hints are directional, not answers. A player who read the discovery beats and conversation seeds has everything they need to succeed on attempt 1.

---

### Orchestrator Changes

New fields on the game loop:

```python
deduction_phase: bool = False
deduction_attempts: int = 0
deduction_max_attempts: int = 3
```

New method: `_check_deduction_trigger()`
- Called at start of every AI turn (before agent acts)
- Returns True when escape item is in inventory AND escape target accessible
- Sets `self.deduction_phase = True` and emits `HUMAN_DEDUCTION` event

New method: `_handle_deduction_submission(answer: str, reasoning: str)`
- Called when frontend sends a deduction submission WebSocket message
- Validates answer against `storyboard.solution`
- On correct: executes final action, narrates victory with reasoning hook, ends game
- On wrong: increments counter, emits hint narration, re-emits `HUMAN_DEDUCTION` prompt
- On third wrong: narrates defeat, reveals motive, ends game

New WebSocket message type (client → server):
```json
{ "type": "deduction", "answer": "Lord Pemberton", "reasoning": "the diary handwriting changed" }
```

The normal turn loop is suspended while `deduction_phase = True`. No AI agent acts during this window.

---

### New Event Kind

```python
class EventKind(str, Enum):
    ...
    HUMAN_DEDUCTION = "human_deduction"  # Pause game, ask human for final answer
```

The `HUMAN_DEDUCTION` event payload:

```json
{
  "kind": "human_deduction",
  "data": {
    "question": "Who murdered Eliza Harwick?",
    "answer_type": "person",
    "attempts_remaining": 3,
    "clues_collected": [
      "The scarf still holds the knot Eliza tied herself — but someone retied it afterwards, tighter.",
      "The diary is Eliza's. The last entry is dated the night of the storm. The handwriting changes halfway through.",
      "The mask is identical to the one in the portrait of the manor's last heir. It was not left here by accident."
    ]
  }
}
```

`clues_collected` is the list of all `discovery_beats` that fired during this game session. This gives the human a summary of the evidence without requiring them to scroll back through the log.

---

### Frontend Changes

New component: `DeductionPanel` (replaces or overlays `HumanTurnPanel` when `HUMAN_DEDUCTION` event arrives)

**Layout:**
```
┌─────────────────────────────────────────────┐
│  THE TEAM HAS ASSEMBLED THE EVIDENCE        │
│  The final call is yours.                   │
├─────────────────────────────────────────────┤
│  EVIDENCE COLLECTED                         │
│  • The scarf knot was retied afterwards...  │
│  • The diary handwriting changes...         │
│  • The mask matches the portrait...         │
├─────────────────────────────────────────────┤
│  Who murdered Eliza Harwick?                │
│  ┌─────────────────────────────────────┐   │
│  │ Your answer...                      │   │
│  └─────────────────────────────────────┘   │
│                                             │
│  How do you know? (optional)                │
│  ┌─────────────────────────────────────┐   │
│  │                                     │   │
│  └─────────────────────────────────────┘   │
│                                             │
│  [  MAKE YOUR ACCUSATION  ]  3 attempts    │
└─────────────────────────────────────────────┘
```

On wrong answer: the hint from the narrator appears above the input, attempt counter decrements, inputs stay active.

On correct answer: panel closes, the final narration plays out as normal (the confirmation beat + victory ending).

---

### Narrator Changes for the Ending

When the human answers correctly, the narrator receives:

```
SCENARIO: {scenario}
SOLUTION CONFIRMED: {solution.answer}
PROOF: {solution.proof_sentence}
PLAYER REASONING: {human's reasoning, or empty}
ENDING GUIDANCE: {ending_guidance.won}
VICTORY HOOK: {solution.victory_narration_hook}

Write 3-4 sentences of closing narration.
- Open with the confirmation beat (the final mechanical action working)
- Name the killer in the narration for the first time
- If PLAYER REASONING is not empty, weave it in — the human saw what mattered
- Close with ENDING GUIDANCE tone
```

The motive (`solution.motive`) is optionally shown as a final POST-NARRATION reveal — a single line after the narration card, styled differently (like a chapter epilogue).

---

### AI-Only Mode (No Human Player)

When the game runs without a human player (all AI agents), the deduction phase is skipped entirely. The AI executes the final action automatically as before. The `ending_guidance.won` narration still fires, but the `victory_narration_hook` is not used (no human reasoning to weave in).

The storyboard `solution` section is still generated (the generator doesn't know at generation time whether there will be a human player), but it is simply never consulted during a fully-AI run.

---

## Generation Prompt Design

The storyboard generator makes **one LLM call** with a rich system prompt and a user prompt containing the full world JSON.

### System prompt principles

1. Genre-agnostic: must work for sci-fi, mystery, heist, asylum horror, nautical curse, etc.
2. Infer genre from `scenario` + `objective` + object descriptions — do not assume murder mystery.
3. Persona adaptation is mandatory — every persona gets a `world_role` that makes sense for this genre.
4. Discovery beats are **one sentence, pre-written, specific** — not vague atmosphere. They must name real objects and real facts from the plot.
5. `vocabulary` lists are **hard constraints** — these are the phrases this character uses in this world. They should actively replace genre-contaminated language.
6. Anti-hallucination: all room_stories keys must be from world JSON rooms. All discovery_beats keys must be from plot-critical objects only. All adapted_personas keys must be character names from the world JSON.

### What goes into the user prompt

- Full `scenario` and `objective`
- All room IDs
- All personas (name, role, skills, backstory)
- Plot-critical objects only (those with `requires_tool`, `requires_code`, `contains_info`, or `connects_to`), excluding `scenic_*`, `gate_*`, `filler_*`
- The lock/unlock dependency graph (which object requires which tool/code, and what produces each code)

---

## Runtime Integration

### At game start (first load)

```
load_world(world_id)
  → check for world_XXX_storyboard.json
  → if missing or stale (world JSON newer): run StoryboardGenerator → save file
  → load storyboard from file
  → inject into GameMasterNarrator at construction
```

### How the narrator uses it

| Narrator call | What changes with storyboard |
|---------------|------------------------------|
| `narrate_opening()` | Uses `plot` fields (victim, protagonist_context, timeline, stakes) as hard facts to mention, not soft tone guidance |
| `narrate_room_entry()` | Uses `room_stories[room_id]` — specific story role of this room |
| `narrate_discovery()` | Retrieves `discovery_beats[object_id]` and emits it directly — **no LLM call for discovery** |
| `narrate_turn()` (dialogue) | Uses `adapted_personas[actor_name].world_role` instead of raw `role`; `vocabulary` list injected as hard constraints |
| `narrate_ending()` | Uses `ending_guidance.won` or `.lost` |

### Discovery beats — critical implementation note

Discovery beats are **retrieved, not generated**. When `narrate_discovery()` is called:
1. Look up `storyboard.discovery_beats[object_id]`
2. If found: emit that sentence directly as the narration card, no LLM call
3. If not found (object wasn't in the storyboard): fall back to the LLM-generated discovery prompt

This is the most important optimization. The moments that matter most are pre-written.

### Conversation seeds — how they get consumed

Each character has 3 seeds. The orchestrator tracks which seeds have been used per character (stored in a set). When a character's turn fires, if they have an unused seed AND it's contextually relevant (they just discovered the related object OR it's their first turn in a new room), inject the seed as a `PENDING_OBSERVATION` field in the dialogue context. The dialogue model uses it as the basis for the character's speech. Mark it consumed after use.

This replaces broadcasting with actual shared observations.

### Adapted personas — where they go in the dialogue prompt

Replace in `build_dialogue_context_package()`:
```
Before:
  - ROLE: Systems Operator
  - SKILLS: electronics, circuits

After:
  - ROLE: Riley reads patterns in human behavior — timing, access, the logic of who could have been where
  - VOCABULARY (use these phrasings): "the timing doesn't add up", "only one person had access to both rooms", ...
```

The raw `role` and `skills` from the persona JSON are never shown to the dialogue model. Only the adapted version.

---

## File Structure

```
backend/app/game/
  world_001.json
  world_001_storyboard.json      ← generated, stored alongside
  world_005.json
  world_005_storyboard.json
  ...
  world_033.json
  world_033_storyboard.json

backend/app/agents/
  storyboard_generator.py        ← StoryboardGenerator class + prompts
  storyboard.py                  ← Storyboard dataclass (schema + solution validation)
  gm_narrator.py                 ← updated to accept Storyboard; new ending hooks
  gm_narrator_prompt.py          ← updated to use adapted persona fields
  plot_generator.py              ← DEPRECATED (was runtime version; remove after storyboard is live)

backend/app/orchestrator/
  loop.py                        ← new deduction_phase logic, _check_deduction_trigger(),
                                    _handle_deduction_submission()

backend/app/schemas/
  events.py                      ← new EventKind.HUMAN_DEDUCTION

backend/app/tools/
  generate_storyboard.py         ← CLI: python -m app.tools.generate_storyboard world_033

frontend/src/components/
  DeductionPanel.tsx             ← new component: question, evidence list, answer + reasoning inputs
  HumanTurnPanel.tsx             ← unchanged (normal human turns still use this)

frontend/src/types.ts            ← new event type: HumanDeductionEvent
```

---

## CLI Usage

```bash
# Generate storyboard for a single world
python -m app.tools.generate_storyboard world_033

# Generate and print to stdout without saving (for inspection)
python -m app.tools.generate_storyboard world_033 --dry-run

# Regenerate even if storyboard already exists
python -m app.tools.generate_storyboard world_033 --force

# Generate for all worlds
python -m app.tools.generate_storyboard --all
```

---

## What Gets Removed After This Is Live

- `backend/app/agents/plot_generator.py` — the runtime `PlotGenerator` and `WorldPlot` classes. The storyboard replaces them entirely.
- `GameMasterNarrator.generate_plot()` — replaced by storyboard injection at construction.
- `extract_lore_excerpt()` in `gm_narrator_prompt.py` — replaced by `Storyboard.lore_excerpt()`.
- `LORE_SYSTEM_PROMPT` and `build_lore_prompt()` in `gm_narrator_prompt.py` — superseded by the storyboard generator.

---

## Open Questions

- [ ] Should the storyboard generator use `json_mode=True` on the Ollama client for guaranteed JSON output? (Recommended — prevents markdown fences and malformed output.)
- [ ] Should storyboards be committed to git alongside world JSONs, or gitignored (generated artifacts)? Lean: **commit them** — they're creative assets, not build outputs.
- [ ] Conversation seeds: consumed globally (any teammate can see it's been used) or per-character only? Lean: **globally tracked** — avoids two characters making the same observation.
- [ ] Should the motive be shown post-game even when the human wins? Lean: **yes** — styled as an epilogue line after the narration card. Satisfying to see your reasoning confirmed.
- [ ] Should wrong-answer hints be narrated (via LLM using the hint text as input) or shown as direct text? Lean: **direct text** — hints are critical information and should not be filtered through an improv LLM that might soften them.
- [ ] In AI-only mode, should the storyboard `solution` still generate? **Yes** — the generator doesn't know how the game will be played. The solution section costs nothing extra (same prompt) and enables future replays with human players.
- [ ] What if the world is not a mystery (sci-fi, heist) — is there always a final deduction? Lean: **yes, always** — the answer type changes (`"object"`, `"location"`, `"code"`) but the mechanic is the same. The question becomes "which system failed?" or "where is the override panel?" The deduction phase is a game design invariant, not a mystery-genre feature.

---

## Status

| Component | Status |
|-----------|--------|
| `Storyboard` dataclass | Not started |
| `StoryboardGenerator` class + prompts | Not started |
| CLI tool (`generate_storyboard.py`) | Not started |
| Auto-generate on first load | Not started |
| Narrator integration (discovery beats, opening, room entry) | Not started |
| Dialogue integration (adapted personas, vocabulary, seeds) | Not started |
| Human deduction phase — orchestrator | Not started |
| Human deduction phase — `HUMAN_DEDUCTION` event kind | Not started |
| Human deduction phase — `DeductionPanel` frontend component | Not started |
| Human deduction phase — answer validation + hint loop | Not started |
| Human deduction phase — victory/defeat narration hooks | Not started |
| Remove `PlotGenerator` (runtime) | Blocked by above |
