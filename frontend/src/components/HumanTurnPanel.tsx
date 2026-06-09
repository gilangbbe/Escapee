import { useState } from "react";
import type { HumanTurnCandidate, HumanTurnData } from "../types";

interface Props {
  turn: HumanTurnData;
  onSubmit: (action: Record<string, unknown>) => void;
}

/**
 * HumanTurnPanel — inline action bar shown at the bottom of the chat column
 * when it's the human player's turn. No modal; the chat stays visible.
 */
export function HumanTurnPanel({ turn, onSubmit }: Props) {
  const [customText, setCustomText] = useState("");
  const [customError, setCustomError] = useState("");

  function pick(candidate: HumanTurnCandidate) {
    onSubmit(candidate.action);
  }

  function submitCustom() {
    const raw = customText.trim();
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw);
      setCustomError("");
      setCustomText("");
      onSubmit(parsed);
    } catch {
      setCustomError("Invalid JSON — try: {\"action\": \"look\"}");
    }
  }

  return (
    <div className="human-turn-bar">
      <div className="human-turn-header">
        <span className="human-turn-you-label">Your turn</span>
        <span className="human-turn-player-name">{turn.player_name}</span>
        {turn.current_goal && (
          <span className="human-turn-goal-hint">· {turn.current_goal}</span>
        )}
      </div>

      <div className="human-turn-chips">
        {turn.candidates.map((c) => (
          <button
            key={c.index}
            type="button"
            className="action-chip"
            onClick={() => pick(c)}
          >
            <span className="action-chip-num">{c.index + 1}</span>
            {c.description}
          </button>
        ))}
      </div>

      <div className="human-custom-row">
        <input
          className="human-custom-input"
          value={customText}
          placeholder='custom action JSON — e.g. {"action":"inspect","target":"old_chest"}'
          onChange={(e) => setCustomText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submitCustom()}
        />
        <button
          type="button"
          className="human-custom-send"
          onClick={submitCustom}
          title="Submit"
        >
          ↵
        </button>
      </div>
      {customError && <div className="human-custom-error">{customError}</div>}
    </div>
  );
}
