import { useMemo } from "react";
import type { EventMessage, Suspect } from "../types";

interface Props {
  suspects: Suspect[];
  events: EventMessage[];
}

export function CaseFilePanel({ suspects, events }: Props) {
  // Proof found when any discovery event carries is_proof: true.
  const proofFound = useMemo(
    () =>
      events.some(
        (e) =>
          e.kind === "discovery" &&
          (e.data as Record<string, unknown> | null | undefined)?.is_proof === true
      ),
    [events]
  );

  // Evidence log: storyboard discovery beats only (kind === "discovery").
  // These are the pre-written narrative sentences from the storyboard, not mechanical codes.
  const evidence = useMemo(
    () => events.filter((e) => e.kind === "discovery"),
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
            {evidence.map((e, i) => {
              const isProof = (e.data as Record<string, unknown> | null | undefined)?.is_proof;
              return (
                <li key={i} className={`evidence-item ${isProof ? "evidence-item-proof" : ""}`}>
                  {isProof ? <><span className="evidence-proof-badge">KEY EVIDENCE</span>{e.text}</> : e.text}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
