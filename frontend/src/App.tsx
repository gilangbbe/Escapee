import { useEffect, useRef, useState } from "react";
import { useGameSocket } from "./useGameSocket";
import { NarrativeFeed } from "./components/NarrativeFeed";
import { PlannerPanel } from "./components/PlannerPanel";
import { StatePanel } from "./components/StatePanel";
import { SetupPanel } from "./components/SetupPanel";
import { PersonaConfigPanel } from "./components/PersonaConfigPanel";
import { HumanTurnPanel } from "./components/HumanTurnPanel";
import { DeductionPanel } from "./components/DeductionPanel";
import type { PersonaDraft } from "./types";

const DEFAULT_WS = "ws://localhost:8000";

const PLAYER_COLORS = ["#58a6ff", "#3fb950", "#f78166", "#d2a8ff", "#ffa657", "#79c0ff"];
const HUMAN_COLOR = "#d29922";

function initialsFor(name: string): string {
  return name
    .split(" ")
    .map((w) => w[0] ?? "")
    .join("")
    .toUpperCase()
    .slice(0, 2);
}

export default function App() {
  const [model, setModel] = useState("qwen2.5:7b");
  const [rounds, setRounds] = useState(30);
  const [narrate, setNarrate] = useState(true);
  const [personas, setPersonas] = useState<PersonaDraft[]>([]);
  const [nudgeText, setNudgeText] = useState("");
  const [debugOpen, setDebugOpen] = useState(false);
  const nudgeInputRef = useRef<HTMLInputElement>(null);
  const feedEndRef = useRef<HTMLDivElement>(null);

  const { state, start, stop, sendNudge, submitHumanAction, submitDeduction, httpBase } =
    useGameSocket(DEFAULT_WS);

  const running = state.status === "running" || state.status === "connecting";

  // Auto-scroll to latest message
  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [state.events.length]);

  function handleSendNudge() {
    const text = nudgeText.trim();
    if (!text || !running) return;
    sendNudge(text);
    setNudgeText("");
    nudgeInputRef.current?.focus();
  }

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
            <button onClick={() => start(model, rounds, narrate, personas)}>
              Start game
            </button>
          ) : (
            <button className="btn-stop" onClick={stop}>
              Stop
            </button>
          )}
          <StatusBadge status={state.status} />
        </div>
      </header>

      {state.error && <div className="error-banner">⚠ {state.error}</div>}
      {state.result && (
        <div className={`result-banner ${state.result.won ? "won" : "lost"}`}>
          {state.result.won ? "🎉 ESCAPED" : "⏳ Did not escape"} —{" "}
          {state.result.reason} in {state.result.turns} turns.
        </div>
      )}

      <main className="game-layout">
        {/* ── LEFT SIDEBAR ── */}
        <aside className="sidebar-left">
          <PersonaConfigPanel
            httpBase={httpBase}
            defaultModel={model}
            disabled={running}
            personas={personas}
            onChange={setPersonas}
          />
          <SetupPanel setup={state.setup} />
        </aside>

        {/* ── CHAT COLUMN ── */}
        <section className="chat-column">
          <div className="chat-feed">
            <NarrativeFeed
              events={state.events}
              personas={state.setup?.players ?? []}
            />
            <div ref={feedEndRef} />
          </div>

          <div className="chat-input-area">
            {state.deductionPhase ? (
              <DeductionPanel
                deduction={state.deductionPhase}
                onSubmit={submitDeduction}
              />
            ) : state.humanTurn ? (
              <HumanTurnPanel
                turn={state.humanTurn}
                onSubmit={submitHumanAction}
              />
            ) : (
              <div className="nudge-area">
                <input
                  ref={nudgeInputRef}
                  className="nudge-area-input"
                  value={nudgeText}
                  placeholder={
                    running
                      ? "Send a hint to the agents…"
                      : "Start a game to send hints"
                  }
                  disabled={!running}
                  onChange={(e) => setNudgeText(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleSendNudge()}
                />
                <button
                  type="button"
                  className="nudge-area-btn"
                  disabled={!running || !nudgeText.trim()}
                  onClick={handleSendNudge}
                >
                  Send hint
                </button>
              </div>
            )}
          </div>
        </section>

        {/* ── RIGHT SIDEBAR ── */}
        <aside className="sidebar-right">
          <div className="crew-strip">
            <h3>Crew</h3>
            {state.setup?.players.map((p, i) => {
              const snap = state.snapshot?.players.find((sp) => sp.id === p.id);
              const color = p.is_human
                ? HUMAN_COLOR
                : PLAYER_COLORS[i % PLAYER_COLORS.length];
              return (
                <div key={p.id} className="crew-card">
                  <div
                    className="crew-avatar"
                    style={{ background: color, boxShadow: `0 0 0 2px ${color}33` }}
                  >
                    {p.is_human ? "YOU" : initialsFor(p.name)}
                  </div>
                  <div className="crew-info">
                    <div className="crew-name">{p.name}</div>
                    <div className="crew-role">{p.role}</div>
                    {snap && (
                      <div className="crew-room" style={{ color }}>
                        {snap.room}
                      </div>
                    )}
                    {snap && snap.inventory.length > 0 && (
                      <div className="crew-inv">
                        {snap.inventory.join(", ")}
                      </div>
                    )}
                  </div>
                  {p.is_human && (
                    <span className="crew-human-badge">you</span>
                  )}
                </div>
              );
            })}
            {!state.setup && (
              <p className="muted">Configure crew and start a game.</p>
            )}
          </div>

          <button
            className="debug-toggle"
            onClick={() => setDebugOpen((o) => !o)}
          >
            {debugOpen ? "▾" : "▸"} Debug panels
          </button>
          {debugOpen && (
            <div className="debug-panels">
              <StatePanel snapshot={state.snapshot} />
              <PlannerPanel
                events={state.events}
                personas={state.setup?.players ?? []}
              />
            </div>
          )}
        </aside>
      </main>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`status status-${status}`}>{status}</span>;
}
