import type { EventMessage, PersonaInfo } from "../types";

interface Props {
  events: EventMessage[];
  personas: PersonaInfo[];
}

const KIND_LABEL: Record<EventMessage["kind"], string> = {
  speech: "💬",
  observation: "👁",
  system: "✦",
  prompt: "🧾",
  decision: "🧠",
  narration: "📖",
};

export function NarrativeFeed({ events, personas }: Props) {
  const nameOf = (id: string | null) => {
    if (!id) return "Narrator";
    return personas.find((p) => p.id === id)?.name ?? id;
  };

  return (
    <div className="feed">
      <h2>Story</h2>
      <div className="feed-scroll">
        {events.length === 0 && (
          <p className="muted">No turns yet. Start a game to watch the story unfold.</p>
        )}
        {events.map((e, i) =>
          e.kind === "narration" ? (
            <p key={i} className="narration">
              {e.text}
            </p>
          ) : (
            <div key={i} className={`event event-${e.kind}`}>
              <span className="event-turn">t{e.turn}</span>
              <span className="event-kind">{KIND_LABEL[e.kind]}</span>
              <span className="event-actor">{nameOf(e.actor_id)}</span>
              <span className="event-text">
                {!e.public && <span className="badge-private">private</span>}
                {e.text}
              </span>
            </div>
          )
        )}
      </div>
    </div>
  );
}
