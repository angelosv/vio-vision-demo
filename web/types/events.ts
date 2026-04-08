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
}

export const demoEvents: MatchEvent[] = [
  {
    id: "1",
    timestamp: "65:12",
    title: "High Press",
    description:
      "Aggressive pressing sequence initiated in the attacking third. 4 players involved.",
    category: "match",
  },
  {
    id: "2",
    timestamp: "62:45",
    title: "Ball in Box",
    description: "Cross from right flank. Defensive clearance successful.",
    category: "key_action",
  },
  {
    id: "3",
    timestamp: "58:20",
    title: "Goal Opportunity",
    description:
      "Shot on target from outside the penalty area. xG: 0.14.",
    category: "critical",
  },
  {
    id: "4",
    timestamp: "45:00",
    title: "Second Half Start",
    description: undefined,
    category: "system",
  },
];
