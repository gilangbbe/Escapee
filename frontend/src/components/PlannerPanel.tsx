import type { EventMessage, PlannerEventData, PersonaInfo } from "../types";

interface Props {
  events: EventMessage[];
  personas: PersonaInfo[];
}

function nameOf(id: string | null, personas: PersonaInfo[]): string {
  if (!id) return "System";
  return personas.find((p) => p.id === id)?.name ?? id;
}

function isPlannerData(data: unknown): data is PlannerEventData {
  if (!data || typeof data !== "object") return false;
  const obj = data as Record<string, unknown>;
  return typeof obj.chosen === "string" && Array.isArray(obj.candidates);
}

export function PlannerPanel({ events, personas }: Props) {
  const plannerEvents = events.filter((e) => e.kind === "planner");
  const latest = plannerEvents[plannerEvents.length - 1] ?? null;
  const payload = latest && isPlannerData(latest.data) ? latest.data : null;

  return (
    <div className="panel planner-panel">
      <h2>Planner Trace</h2>
      {!latest && <p className="muted">No planner interventions yet.</p>}

      {latest && (
        <>
          <div className="planner-meta">
            <div className="muted">
              turn {latest.turn} · actor {nameOf(latest.actor_id, personas)}
            </div>
            <div className="planner-chosen">chosen: {payload?.chosen ?? latest.text}</div>
            {payload?.reason && <div className="muted">reason: {payload.reason}</div>}
          </div>

          <div className="planner-table-wrap">
            <table className="planner-table">
              <thead>
                <tr>
                  <th>rank</th>
                  <th>score</th>
                  <th>action</th>
                </tr>
              </thead>
              <tbody>
                {(payload?.candidates ?? [])
                  .slice()
                  .sort((a, b) => b.score - a.score)
                  .map((c) => (
                    <tr key={`${c.rank}-${c.action}`}>
                      <td>{c.rank}</td>
                      <td>{c.score.toFixed(2)}</td>
                      <td title={c.rationale}>{c.action}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
