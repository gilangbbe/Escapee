import { useCallback, useRef, useState } from "react";
import type {
  EventMessage,
  ResultMessage,
  ServerMessage,
  SetupMessage,
  StateMessage,
} from "./types";

export type ConnStatus = "idle" | "connecting" | "running" | "finished" | "error";

interface GameState {
  status: ConnStatus;
  setup: SetupMessage | null;
  events: EventMessage[];
  snapshot: StateMessage | null;
  result: ResultMessage | null;
  error: string | null;
}

const INITIAL: GameState = {
  status: "idle",
  setup: null,
  events: [],
  snapshot: null,
  result: null,
  error: null,
};

/**
 * useGameSocket — opens a WebSocket to the backend, runs one game, and
 * accumulates the streamed setup / event / state / result messages.
 */
export function useGameSocket(wsBaseUrl: string) {
  const [state, setState] = useState<GameState>(INITIAL);
  const socketRef = useRef<WebSocket | null>(null);

  const start = useCallback(
    (model: string, rounds: number, narrate: boolean = true) => {
      socketRef.current?.close();
      setState({ ...INITIAL, status: "connecting" });

      const url = `${wsBaseUrl}/ws/game?model=${encodeURIComponent(
        model
      )}&rounds=${rounds}&narrate=${narrate ? "true" : "false"}`;
      const ws = new WebSocket(url);
      socketRef.current = ws;

      ws.onopen = () => setState((s) => ({ ...s, status: "running" }));

      ws.onmessage = (ev) => {
        const msg: ServerMessage = JSON.parse(ev.data);
        setState((s) => {
          switch (msg.type) {
            case "setup":
              return { ...s, setup: msg };
            case "event":
              return { ...s, events: [...s.events, msg] };
            case "state":
              return { ...s, snapshot: msg };
            case "result":
              return { ...s, result: msg, status: "finished" };
            case "error":
              return { ...s, error: msg.message, status: "error" };
            default:
              return s;
          }
        });
      };

      ws.onerror = () =>
        setState((s) =>
          s.status === "finished"
            ? s
            : { ...s, status: "error", error: "WebSocket error — is the backend running?" }
        );

      ws.onclose = () =>
        setState((s) =>
          s.status === "running" ? { ...s, status: "finished" } : s
        );
    },
    [wsBaseUrl]
  );

  const stop = useCallback(() => {
    socketRef.current?.close();
    socketRef.current = null;
  }, []);

  return { state, start, stop };
}
