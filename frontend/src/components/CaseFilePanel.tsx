import { useMemo } from "react";
import type { EventMessage, Suspect } from "../types";

interface Props {
  suspects: Suspect[];
  proofObjectId: string;
  events: EventMessage[];
}

export function CaseFilePanel({ suspects, proofObjectId, events }: Props) {
  // Derive proof-found status from system events mentioning the proof object.
  const proofFound = useMemo(() => {
    if (!proofObjectId) return false;
    return events.some(
      (e) =>
        e.kind === "system" &&
        e.text.includes(proofObjectId.replace(/_/g, " ").toLowerCase()) ||
        (e.kind === "system" && e.text.includes(proofObjectId))
    );
  }, [events, proofObjectId]);

  // Evidence log: system events that signal real progress (contain "✓").
  const evidence = useMemo(
    () => events.filter((e) => e.kind === "system" && e.text.startsWith("✓")),
    [events]
  );

  const hasSuspects = suspects.length > 0;

  return (
    <div className="case-file">
      {/* ── Mystery status ── */}
      <div className={`mystery-status ${proofFound ? "mystery-status-revealed" : "mystery-status-open"}`}>
        {proofFound ? (
          <>
            <span className="mystery-status-dot" />
            Proof found — make your accusation
          </>
        ) : (
          <>
            <span className="mystery-status-dot" />
            Investigating…
          </>
        )}
      </div>

      {/* ── Suspects board ── */}
      {hasSuspects && (
        <section className="case-section">
          <h3 className="case-section-title">Suspects</h3>
          <div className="suspects-list">
            {suspects.map((s) => (
              <div key={s.name} className="suspect-entry">
                <div className="suspect-entry-name">{s.name}</div>
                {s.connection_to_victim && (
                  <div className="suspect-entry-connection">{s.connection_to_victim}</div>
                )}
                {s.apparent_motive && (
                  <div className="suspect-entry-motive">
                    <span className="suspect-motive-label">motive</span>
                    {s.apparent_motive}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── Evidence log ── */}
      <section className="case-section">
        <h3 className="case-section-title">
          Evidence
          {evidence.length > 0 && (
            <span className="evidence-count">{evidence.length}</span>
          )}
        </h3>
        {evidence.length === 0 ? (
          <p className="case-empty">No evidence collected yet.</p>
        ) : (
          <ul className="evidence-list">
            {evidence.map((e, i) => (
              <li key={i} className="evidence-item">
                {e.text.replace(/^✓\s*/, "")}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
