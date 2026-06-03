import type { StateMessage } from "../types";

interface Props {
  snapshot: StateMessage | null;
}

export function StatePanel({ snapshot }: Props) {
  if (!snapshot) {
    return (
      <div className="panel">
        <h2>World State</h2>
        <p className="muted">Waiting for the first snapshot…</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h2>
        World State <span className="muted">turn {snapshot.turn}</span>
      </h2>

      <section>
        <h3>Players</h3>
        {snapshot.players.map((p) => (
          <div key={p.id} className="player-row">
            <strong>{p.name}</strong> — <span className="room">{p.room}</span>
            <div className="muted">
              holding: {p.inventory.length ? p.inventory.join(", ") : "—"}
            </div>
          </div>
        ))}
      </section>

      <section>
        <h3>Objects</h3>
        <div className="objects">
          {snapshot.objects.map((o) => (
            <div key={o.id} className={`obj obj-${o.state}`} title={o.description}>
              <span className="obj-id">{o.id}</span>
              <span className="obj-state">{o.state}</span>
              <span className="muted">@ {o.room ?? o.location}</span>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h3>Systems</h3>
        <div className="muted">
          accessible rooms: {snapshot.accessible_rooms.join(", ") || "—"}
        </div>
        <div className="muted">
          power: {snapshot.power_flags.join(", ") || "none"}
        </div>
        <div className="muted">
          win when <code>{snapshot.win_condition.object_id}</code> is{" "}
          <code>{snapshot.win_condition.state}</code>
        </div>
      </section>
    </div>
  );
}
