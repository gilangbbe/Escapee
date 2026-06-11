import { useState } from "react";
import type { DeductionData, Suspect } from "../types";

interface Props {
  deduction: DeductionData;
  suspects: Suspect[];
  onSubmit: (answer: string) => void;
}

export function DeductionPanel({ deduction, suspects, onSubmit }: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const [customAnswer, setCustomAnswer] = useState("");

  const attemptsLeft = deduction.max_attempts - deduction.attempt + 1;
  const hasSuspects = suspects.length > 0;

  function submit(answer: string) {
    const trimmed = answer.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
    setSelected(null);
    setCustomAnswer("");
  }

  return (
    <div className="deduction-panel">
      <div className="deduction-header">
        <span className="deduction-icon">🔍</span>
        <span className="deduction-label">Make your accusation</span>
        <span className="deduction-attempts">
          {attemptsLeft} attempt{attemptsLeft !== 1 ? "s" : ""} left
        </span>
      </div>

      <div className="deduction-question">{deduction.question || "Who is responsible?"}</div>

      {deduction.hint && (
        <div className="deduction-hint">
          <span className="deduction-hint-label">Hint</span>
          {deduction.hint}
        </div>
      )}

      {hasSuspects ? (
        <>
          <div className="suspect-picker">
            {suspects.map((s) => (
              <button
                key={s.name}
                className={`suspect-card ${selected === s.name ? "suspect-card-selected" : ""}`}
                onClick={() => setSelected(selected === s.name ? null : s.name)}
              >
                <div className="suspect-card-name">{s.name}</div>
                {s.apparent_motive && (
                  <div className="suspect-card-motive">{s.apparent_motive}</div>
                )}
              </button>
            ))}
          </div>
          <button
            className="deduction-submit"
            disabled={!selected}
            onClick={() => selected && submit(selected)}
          >
            Accuse {selected ?? "…"}
          </button>
        </>
      ) : (
        <div className="deduction-input-row">
          <input
            className="deduction-input"
            value={customAnswer}
            onChange={(e) => setCustomAnswer(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit(customAnswer)}
            placeholder="Name the killer…"
            autoFocus
          />
          <button
            className="deduction-submit"
            onClick={() => submit(customAnswer)}
            disabled={!customAnswer.trim()}
          >
            Accuse
          </button>
        </div>
      )}
    </div>
  );
}
