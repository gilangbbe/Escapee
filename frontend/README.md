# Escapee — Frontend (React + Vite + TypeScript)

Text-only spectator UI for the Multi-LLM Escape Room simulation. It connects to
the backend over a WebSocket, watches a game unfold, and shows three columns:

- **Scenario / Crew** — the GM's setting and player personas.
- **Narrative** — the live timeline (speech 💬, observations 👁, system ✦).
- **World State** — a grounded snapshot of players, objects, power, and the win
  condition, refreshed after every turn.

## Run

Prerequisites: the backend running on `http://localhost:8000` (see
`../backend`), and a local Ollama server with the chosen model pulled
(e.g. `ollama pull llama3`).

```bash
npm install
npm run dev          # http://localhost:5173
```

In the top bar, set the Ollama **model** and max **rounds**, then **Start game**.

## Build

```bash
npm run build        # type-check + production bundle into dist/
npm run preview
```

## Configuration

The WebSocket base URL defaults to `ws://localhost:8000` (see `DEFAULT_WS` in
`src/App.tsx`). The backend streams JSON messages of type `setup`, `event`,
`state`, `result`, and `error`; their shapes are in `src/types.ts`.
