// ─── Native microservices payload types ──────────────────────────────

export type ObjectType = "Player" | "Goalkeeper" | "Referee";

export interface Position {
  x: number; // normalized 0-1 (left→right)
  y: number; // normalized 0-1 (top→bottom)
}

export interface BoundingBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface TrackedObject {
  track_id: number;
  type: ObjectType;
  team: "home" | "away" | "ref";
  jersey_number: string; // may be empty
  position: Position;
  bbox: BoundingBox;
  confidence: number;
  team_color: string; // hex #RRGGBB
}

export interface Ball {
  position: Position;
  bbox: BoundingBox;
  confidence: number;
}

export interface Possession {
  team: "home" | "away" | null;
  home_percent: number;
  away_percent: number;
}

export type EventType =
  // Heuristic events
  | "possession_change" | "shot_on_goal" | "out_of_bounds" | "corner" | "fast_break"
  | "foul" | "stoppage"
  // GPT-4o detected
  | "goal" | "goal_chance" | "celebration"
  | "yellow_card" | "red_card" | "penalty" | "offside"
  | "substitution" | "free_kick"
  | "normal_play" | "crowd_reaction"
  // Context engine (Sprint 4)
  | "match_narrative" | "player_milestone"
  // System / engagement
  | "sentiment_change" | "tension_spike" | "possession_milestone"
  | "poll" | "sentiment_prompt" | "product"
  | "system" | "error";

// Per-team accumulated stats
export interface TeamStats {
  goals: number;
  shots: number;
  corners: number;
  fouls: number;
  yellow_cards: number;
  red_cards: number;
  substitutions: number;
}

// Momentum derived from rolling possession window
export interface Momentum {
  direction: "home_dominant" | "away_dominant" | "balanced";
  home_percent: number;
  away_percent: number;
  window_sec: number;
}

// Full MatchState snapshot attached to every frame payload
export interface MatchState {
  minute: number | null;
  score: { home: number; away: number } | null;
  teams: { home: TeamStats; away: TeamStats };
  momentum: Momentum;
  recent_events: Array<{
    time_sec: number;
    type: string;
    team: string | null;
    tension: number;
    confirmed: boolean;
  }>;
  players_flagged: Array<{
    track_id: number;
    team: string;
    jersey: string;
    yellow_cards: number;
    fouls: number;
  }>;
}

export interface MatchEventData {
  type: EventType;
  description?: string;
  tension_score: number;
  confirmed: boolean;
  team?: string;
}

export interface MatchFramePayload {
  type: "event";
  session_id: string;
  frame_index: number;
  timestamp_ms: number;
  frame_time_sec: number;
  tracks: TrackedObject[];
  ball: Ball | null;
  team_colors: string[];
  possession: Possession;
  event: MatchEventData | null;
  crowd_intensity: number;
  sentiment: "calm" | "tense" | "euphoric" | "frustrated" | null;
  context?: MatchState;
}

// ─── Frontend-facing types ────────────────────────────────────────────

/** Visual category used to color/filter events in the sidebar. */
export type EventCategory =
  | "match" | "key_action" | "critical" | "card" | "foul" | "set_piece"
  | "possession" | "stoppage" | "sentiment"
  | "narrative" | "milestone"
  | "poll" | "sentiment_prompt" | "product"
  | "system";

export interface MatchEvent {
  id: string;
  timestamp: string; // "MM:SS"
  title: string;
  description?: string;
  category: EventCategory;
  eventType: EventType;
  tensionScore?: number;
  confirmed?: boolean;
  team?: string;
}

export interface TensionPoint {
  time: number; // seconds
  score: number; // 0-10
}

export interface SmartPoll {
  poll_id: string;
  question: string;
  options: string[];
  duration: number;
}

export interface HighlightMoment {
  id: string;
  timeSec: number;
  timestamp: string;
  eventType: string;
  description: string;
  tensionScore: number;
  category: EventCategory;
  confirmed?: boolean;
}
