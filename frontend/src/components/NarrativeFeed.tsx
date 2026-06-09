import { useMemo } from "react";
import type { EventMessage, PersonaInfo } from "../types";

interface Props {
  events: EventMessage[];
  personas: PersonaInfo[];
}

const PLAYER_COLORS = [
  "#58a6ff",
  "#3fb950",
  "#f78166",
  "#d2a8ff",
  "#ffa657",
  "#79c0ff",
];
const HUMAN_COLOR = "#d29922";
const SYSTEM_COLOR = "#8b949e";

function colorFor(actorId: string | null, personas: PersonaInfo[]): string {
  if (!actorId) return SYSTEM_COLOR;
  const idx = personas.findIndex((p) => p.id === actorId);
  if (idx === -1) return SYSTEM_COLOR;
  return personas[idx].is_human ? HUMAN_COLOR : PLAYER_COLORS[idx % PLAYER_COLORS.length];
}

function initialsFor(name: string): string {
  return name
    .split(" ")
    .map((w) => w[0] ?? "")
    .join("")
    .toUpperCase()
    .slice(0, 2);
}

type SepItem = { type: "sep"; turn: number; key: string };
type EventItem = { type: "event"; event: EventMessage; key: string };
type RenderItem = SepItem | EventItem;

const SKIP_KINDS = new Set(["planner", "prompt", "decision", "human_turn"]);

export function NarrativeFeed({ events, personas }: Props) {
  const items = useMemo<RenderItem[]>(() => {
    const result: RenderItem[] = [];
    let lastTurn = -1;
    events.forEach((event, i) => {
      if (SKIP_KINDS.has(event.kind)) return;
      if (event.turn !== lastTurn) {
        result.push({ type: "sep", turn: event.turn, key: `sep-${event.turn}-${i}` });
        lastTurn = event.turn;
      }
      result.push({ type: "event", event, key: `e-${i}` });
    });
    return result;
  }, [events]);

  if (items.length === 0) {
    return (
      <div className="chat-empty">
        <p className="muted">Waiting for the story to begin…</p>
      </div>
    );
  }

  return (
    <>
      {items.map((item) =>
        item.type === "sep" ? (
          <div key={item.key} className="chat-turn-sep">
            <span>Turn {item.turn}</span>
          </div>
        ) : (
          <ChatEvent key={item.key} event={item.event} personas={personas} />
        )
      )}
    </>
  );
}

function ChatEvent({ event, personas }: { event: EventMessage; personas: PersonaInfo[] }) {
  const color = colorFor(event.actor_id, personas);
  const persona = event.actor_id ? personas.find((p) => p.id === event.actor_id) : null;
  const name = persona?.name ?? (event.actor_id ? event.actor_id : "System");
  const isHuman = persona?.is_human ?? false;
  const isPrivate = !event.public && !!event.audience_id;

  if (event.kind === "narration") {
    return <div className="chat-narration">{event.text}</div>;
  }

  if (event.kind === "system") {
    return (
      <div className="chat-system-pill">
        <span>{event.text}</span>
      </div>
    );
  }

  if (event.kind === "observation") {
    return (
      <div className="chat-observation">
        <span className="chat-obs-icon">👁</span>
        <div className="chat-obs-body">
          {isPrivate && <span className="badge-private">only you</span>}
          {event.text}
        </div>
      </div>
    );
  }

  if (event.kind === "speech") {
    const initials = initialsFor(name);
    return (
      <div className={`chat-row ${isHuman ? "chat-row-right" : "chat-row-left"}`}>
        {!isHuman && (
          <div
            className="chat-avatar"
            style={{ background: color, boxShadow: `0 0 0 2px ${color}33` }}
            title={name}
          >
            {initials}
          </div>
        )}
        <div className="chat-bubble-wrap">
          <div className="chat-sender" style={{ color }}>
            {name}
          </div>
          <div
            className={`chat-bubble ${isHuman ? "chat-bubble-human" : "chat-bubble-ai"}`}
            style={
              isHuman
                ? { borderColor: `${color}66`, background: `${color}18` }
                : undefined
            }
          >
            {isPrivate && <span className="badge-private">private</span>}
            {event.text}
          </div>
        </div>
        {isHuman && (
          <div
            className="chat-avatar chat-avatar-right"
            style={{ background: color, boxShadow: `0 0 0 2px ${color}33` }}
            title={name}
          >
            YOU
          </div>
        )}
      </div>
    );
  }

  // Fallback for unhandled kinds
  return (
    <div className="chat-generic">
      <span className="muted">[{event.kind}]</span> {event.text}
    </div>
  );
}
