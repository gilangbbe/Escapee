import type { SetupMessage } from "../types";

interface Props {
  setup: SetupMessage | null;
}

export function SetupPanel({ setup }: Props) {
  if (!setup) return null;
  return (
    <div className="panel setup">
      <h2>{setup.scenario ? "Scenario" : "Mission"}</h2>
      <p className="scenario">{setup.scenario}</p>
      <p className="objective">
        <strong>Objective:</strong> {setup.objective}
      </p>

      <h3>Crew</h3>
      {setup.players.map((p) => (
        <div key={p.id} className="persona">
          <strong>{p.name}</strong> <span className="muted">({p.role})</span>
          <div className="muted">skills: {p.skills.join(", ")}</div>
        </div>
      ))}
    </div>
  );
}
