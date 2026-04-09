export interface Detection {
  label: string;
  confidence: number;
  box: [number, number, number, number]; // [x1, y1, x2, y2] normalized 0–1
  color?: string;  // hex jersey color
  team?: number;   // 0 or 1
}

export type EventCategory =
  | "match" | "key_action" | "critical" | "card" | "foul" | "set_piece" | "system"
  | "poll" | "sentiment_prompt" | "product";

export interface MatchEvent {
  id: string;
  timestamp: string; // e.g. "65:12"
  title: string;
  description?: string;
  category: EventCategory;
  tensionScore?: number;
}

export interface TensionPoint {
  time: number;  // seconds
  score: number; // 0–10
}

export interface SmartPoll {
  poll_id: string;
  question: string;
  options: string[];
  duration: number; // seconds
}

export interface HighlightMoment {
  id: string;
  timeSec: number;
  timestamp: string; // "MM:SS"
  eventType: string;
  description: string;
  tensionScore: number;
  category: EventCategory;
  confirmed?: boolean;
}
