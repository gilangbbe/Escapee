// Message shapes streamed by the backend over the WebSocket. These mirror the
// dicts produced by backend/app/web/serializers.py.

export interface SetupMessage {
  type: "setup";
  scenario: string;
  objective: string;
  rooms: string[];
  rules: string[];
  players: PersonaInfo[];
}

export interface PersonaInfo {
  id: string;
  name: string;
  role: string;
  skills: string[];
  backstory: string;
  personality?: string;
  model?: string | null;
}

export type EventKind = "speech" | "observation" | "system" | "planner" | "prompt" | "decision" | "narration";

export interface PlannerCandidate {
  rank: number;
  action: string;
  score: number;
  rationale: string;
}

export interface PlannerEventData {
  reason: string | null;
  chosen: string;
  candidates: PlannerCandidate[];
}

export interface EventMessage {
  type: "event";
  kind: EventKind;
  actor_id: string | null;
  text: string;
  turn: number;
  public: boolean;
  audience_id: string | null;
  data?: PlannerEventData | null;
}

export interface ObjectSnapshot {
  id: string;
  location: string;
  room: string | null;
  state: string;
  takeable: boolean;
  description: string;
}

export interface PlayerSnapshot {
  id: string;
  name: string;
  room: string;
  inventory: string[];
}

export interface StateMessage {
  type: "state";
  turn: number;
  rooms: string[];
  accessible_rooms: string[];
  objects: ObjectSnapshot[];
  players: PlayerSnapshot[];
  power_flags: string[];
  finished: boolean;
  won: boolean;
  win_condition: { object_id: string; state: string };
}

export interface ResultMessage {
  type: "result";
  won: boolean;
  turns: number;
  reason: string;
}

export interface ErrorMessage {
  type: "error";
  message: string;
}

export type ServerMessage =
  | SetupMessage
  | EventMessage
  | StateMessage
  | ResultMessage
  | ErrorMessage;

// Editable persona used by the pre-game crew configurator. Mirrors the fields
// accepted by backend PlayerPersona (model/temperature are per-player overrides).
export interface PersonaDraft {
  id: string;
  name: string;
  role: string;
  skills: string[];
  backstory: string;
  personality: string;
  model: string;
  temperature: number | null;
}

// Bootstrap payload from GET /api/personas.
export interface PersonaCatalogEntry {
  key: string;
  name: string;
  role: string;
  skills: string[];
  backstory: string;
  personality: string;
  model: string | null;
  temperature: number | null;
}

export interface PersonasBootstrap {
  default_model: string;
  models: string[];
  catalog: PersonaCatalogEntry[];
  roster: PersonaInfo[];
}

