import { useState } from "react";
import { useGameSocket } from "./useGameSocket";
import { NarrativeFeed } from "./components/NarrativeFeed";
import { StatePanel } from "./components/StatePanel";
import { SetupPanel } from "./components/SetupPanel";

const DEFAULT_WS = "ws://localhost:8000";

export default function App() {
  const [model, setModel] = useState("qwen2.5:7b");
  const [rounds, setRounds] = useState(30);
  const [narrate, setNarrate] = useState(true);
  const { state, start, stop } = useGameSocket(DEFAULT_WS);

  const running = state.status === "running" || state.status === "connecting";

  return (
    <div className="app">
      <header className="topbar">
        <h1>🛰 Multi-LLM Escape Room</h1>
        <div className="controls">
          <label>
            model
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              disabled={running}
            />
          </label>
          <label>
            rounds
            <input
              type="number"
              min={1}
              max={100}
              value={rounds}
              onChange={(e) => setRounds(Number(e.target.value))}
              disabled={running}
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={narrate}
              onChange={(e) => setNarrate(e.target.checked)}
              disabled={running}
            />
            narrate
          </label>
          {!running ? (
            <button onClick={() => start(model, rounds, narrate)}>Start game</button>
          ) : (
            <button onClick={stop}>Stop</button>
          )}
          <StatusBadge status={state.status} />
        </div>
      </header>

      {state.error && <div className="error-banner">⚠ {state.error}</div>}

      {state.result && (
        <div className={`result-banner ${state.result.won ? "won" : "lost"}`}>
          {state.result.won ? "🎉 The team ESCAPED" : "⏳ The team did not escape"} —{" "}
          {state.result.reason} in {state.result.turns} turns.
        </div>
      )}

      <main className="layout">
        <aside className="col-left">
          <SetupPanel setup={state.setup} />
        </aside>
        <section className="col-center">
          <NarrativeFeed events={state.events} personas={state.setup?.players ?? []} />
        </section>
        <aside className="col-right">
          <StatePanel snapshot={state.snapshot} />
        </aside>
      </main>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`status status-${status}`}>{status}</span>;
}
