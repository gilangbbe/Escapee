import { useState } from "react";
import type { DeductionData } from "../types";

interface Props {
  deduction: DeductionData;
  onSubmit: (answer: string) => void;
}

/**
 * DeductionPanel — shown when the AI agents have mechanically solved the
 * puzzle and the game pauses for the human to name the answer (killer,
 * sabotaged system, stolen object, etc.).
 *
 * Replaces the nudge bar at the bottom of the chat column.
 */
export function DeductionPanel({ deduction, onSubmit }: Props) {
  const [answer, setAnswer] = useState("");

  function submit() {
    const trimmed = answer.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
    setAnswer("");
  }

  const attemptsLeft = deduction.max_attempts - deduction.attempt + 1;

  return (
    <div className="deduction-panel">
      <div className="deduction-header">
        <span className="deduction-icon">🔍</span>
        <span className="deduction-label">Your final deduction</span>
        <span className="deduction-attempts">
          {attemptsLeft} attempt{attemptsLeft !== 1 ? "s" : ""} left
        </span>
      </div>

      <div className="deduction-question">{deduction.question}</div>

      {deduction.hint && (
        <div className="deduction-hint">
          <span className="deduction-hint-label">Hint</span>
          {deduction.hint}
        </div>
      )}

      <div className="deduction-input-row">
        <input
          className="deduction-input"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="Name the answer…"
          autoFocus
        />
        <button
          type="button"
          className="deduction-submit"
          onClick={submit}
          disabled={!answer.trim()}
        >
          Accuse
        </button>
      </div>
    </div>
  );
}
