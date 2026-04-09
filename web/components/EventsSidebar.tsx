"use client";

import { useState } from "react";
import type { MatchEvent, EventCategory } from "@/types/events";
import { EventItem } from "./EventItem";

interface EventsSidebarProps {
  events: MatchEvent[];
}

const CATEGORY_FILTERS: { label: string; value: EventCategory | "all" }[] = [
  { label: "All", value: "all" },
  { label: "Critical", value: "critical" },
  { label: "Cards", value: "card" },
  { label: "Fouls", value: "foul" },
  { label: "Set Pieces", value: "set_piece" },
  { label: "Key", value: "key_action" },
  { label: "Match", value: "match" },
  { label: "Polls", value: "poll" },
  { label: "Sentiment", value: "sentiment_prompt" },
  { label: "Product", value: "product" },
  { label: "System", value: "system" },
];

export function EventsSidebar({ events }: EventsSidebarProps) {
  const [filter, setFilter] = useState<EventCategory | "all">("all");
  const [search, setSearch] = useState("");

  const filtered = events.filter((ev) => {
    if (filter !== "all" && ev.category !== filter) return false;
    if (search) {
      const q = search.toLowerCase();
      return (
        ev.title.toLowerCase().includes(q) ||
        (ev.description?.toLowerCase().includes(q) ?? false)
      );
    }
    return true;
  });

  const handleExport = (format: "json" | "csv") => {
    let content: string;
    let mime: string;
    let ext: string;

    if (format === "json") {
      content = JSON.stringify(events, null, 2);
      mime = "application/json";
      ext = "json";
    } else {
      const header = "timestamp,title,category,tensionScore,description";
      const rows = events.map((e) =>
        `"${e.timestamp}","${e.title}","${e.category}",${e.tensionScore ?? ""},"${(e.description ?? "").replace(/"/g, '""')}"`
      );
      content = [header, ...rows].join("\n");
      mime = "text/csv";
      ext = "csv";
    }

    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `vio-events-${Date.now()}.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <aside className="w-96 glass-panel rounded-xl flex flex-col overflow-hidden shrink-0">
      {/* Header */}
      <div className="p-3 border-b border-brand-border bg-brand-panel/50">
        <div className="flex justify-between items-center mb-2">
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <span className="text-brand-muted text-xs">▤</span>
            Events
            <span className="text-[10px] text-brand-muted font-normal">({filtered.length})</span>
          </h2>
          <div className="flex gap-1">
            <button
              onClick={() => handleExport("json")}
              className="px-2 py-1 rounded bg-white/5 text-[10px] hover:bg-white/10 text-brand-muted"
              title="Export JSON"
            >
              JSON
            </button>
            <button
              onClick={() => handleExport("csv")}
              className="px-2 py-1 rounded bg-white/5 text-[10px] hover:bg-white/10 text-brand-muted"
              title="Export CSV"
            >
              CSV
            </button>
          </div>
        </div>

        {/* Search */}
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search events..."
          className="w-full bg-brand-panel/50 border border-brand-border rounded-lg py-1 px-3 text-xs text-white placeholder-brand-muted focus:outline-none focus:border-brand-primary/50 mb-2"
        />

        {/* Category filters */}
        <div className="flex gap-1 flex-wrap">
          {CATEGORY_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              className={`px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${
                filter === f.value
                  ? "bg-brand-primary/20 text-brand-primary border border-brand-primary/30"
                  : "bg-white/5 text-brand-muted hover:bg-white/10 border border-transparent"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* Event list */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {filtered.length > 0 ? (
          filtered.map((ev) => <EventItem key={ev.id} event={ev} />)
        ) : (
          <div className="text-center text-xs text-brand-muted py-8">
            {events.length === 0 ? "No events yet" : "No events match filters"}
          </div>
        )}
      </div>
    </aside>
  );
}
