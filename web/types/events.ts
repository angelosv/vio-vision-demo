export interface Detection {
  label: string;
  confidence: number;
  box: [number, number, number, number]; // [x1, y1, x2, y2] normalized 0–1
}

export type EventCategory = "match" | "key_action" | "critical" | "system";

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
