import { useCallback, useEffect, useRef, useState } from "react";
import type {
  EventMessage,
  PersonaDraft,
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

function httpBaseFromWs(wsBaseUrl: string): string {
  if (wsBaseUrl.startsWith("ws://")) {
    return `http://${wsBaseUrl.slice("ws://".length)}`;
  }
  if (wsBaseUrl.startsWith("wss://")) {
    return `https://${wsBaseUrl.slice("wss://".length)}`;
  }
  return wsBaseUrl;
}

/** Serialize the UI roster into the backend PlayerPersona shape, dropping
 * blank per-player overrides so the team-wide model/temperature win. */
function rosterParam(personas: PersonaDraft[]): string | null {
  const named = personas.filter((p) => p.name.trim());
  if (named.length === 0) return null;
  const payload = named.map((p) => {
    const entry: Record<string, unknown> = {
      name: p.name.trim(),
      role: p.role.trim(),
      skills: p.skills,
      backstory: p.backstory,
      personality: p.personality,
      gender: p.gender.trim(),
    };
    if (p.model.trim()) entry.model = p.model.trim();
    if (p.temperature != null) entry.temperature = p.temperature;
    return entry;
  });
  return JSON.stringify(payload);
}


/**
 * useGameSocket — opens a WebSocket to the backend, runs one game, and
 * accumulates the streamed setup / event / state / result messages.
 */
export function useGameSocket(wsBaseUrl: string) {
  const [state, setState] = useState<GameState>(INITIAL);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadSetup() {
      try {
        const res = await fetch(`${httpBaseFromWs(wsBaseUrl)}/api/setting`);
        if (!res.ok) {
          throw new Error(`setup request failed: ${res.status}`);
        }
        const setup: SetupMessage = await res.json();
        if (!cancelled) {
          setState((s) => ({ ...s, setup }));
        }
      } catch {
        // Leave setup null; the websocket setup message will still populate it.
      }
    }

    void loadSetup();

    return () => {
      cancelled = true;
    };
  }, [wsBaseUrl]);

  const start = useCallback(
    (
      model: string,
      rounds: number,
      narrate: boolean = true,
      personas: PersonaDraft[] = []
    ) => {
      socketRef.current?.close();
      setState((s) => ({
        ...INITIAL,
        setup: s.setup,
        status: "connecting",
      }));

      let url = `${wsBaseUrl}/ws/game?model=${encodeURIComponent(
        model
      )}&rounds=${rounds}&narrate=${narrate ? "true" : "false"}`;
      const roster = rosterParam(personas);
      if (roster) {
        url += `&personas=${encodeURIComponent(roster)}`;
      }
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

  return { state, start, stop, httpBase: httpBaseFromWs(wsBaseUrl) };
}
