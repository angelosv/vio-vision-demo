import { MatchEvent } from "@/types/events";
import { EventItem } from "./EventItem";

interface EventsSidebarProps {
  events: MatchEvent[];
}

export function EventsSidebar({ events }: EventsSidebarProps) {
  return (
    <aside className="w-96 glass-panel rounded-xl flex flex-col overflow-hidden shrink-0">
      <div className="p-4 border-b border-brand-border flex justify-between items-center bg-brand-panel/50">
        <h2 className="text-sm font-semibold flex items-center gap-2">
          <span className="text-brand-muted text-xs">▤</span>
          Live Event Log
        </h2>
        <div className="flex gap-1">
          <button className="w-6 h-6 rounded bg-white/5 flex items-center justify-center text-xs hover:bg-white/10 text-brand-muted">
            ≡
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {events.map((ev) => (
          <EventItem key={ev.id} event={ev} />
        ))}
      </div>
    </aside>
  );
}
