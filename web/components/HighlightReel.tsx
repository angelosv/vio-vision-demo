"use client";

import type { HighlightMoment } from "@/types/events";

interface HighlightReelProps {
  highlights: HighlightMoment[];
  onSeek: (timeSec: number) => void;
  onClose: () => void;
}

function categoryColor(category: string): { bg: string; text: string; dot: string } {
  switch (category) {
    case "critical":
      return { bg: "bg-red-500/10", text: "text-red-400", dot: "bg-red-400" };
    case "card":
      return { bg: "bg-yellow-500/10", text: "text-yellow-400", dot: "bg-yellow-400" };
    case "foul":
      return { bg: "bg-orange-500/10", text: "text-orange-400", dot: "bg-orange-400" };
    case "set_piece":
      return { bg: "bg-cyan-500/10", text: "text-cyan-400", dot: "bg-cyan-400" };
    case "key_action":
      return { bg: "bg-brand-primary/10", text: "text-brand-primary", dot: "bg-brand-primary" };
    default:
      return { bg: "bg-white/5", text: "text-brand-muted", dot: "bg-brand-muted" };
  }
}

function tensionBar(score: number): { color: string; width: string } {
  const width = `${score * 10}%`;
  if (score >= 8) return { color: "bg-red-400", width };
  if (score >= 5) return { color: "bg-yellow-400", width };
  return { color: "bg-green-400", width };
}

function formatEventType(eventType: string): string {
  return eventType
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function HighlightReel({ highlights, onSeek, onClose }: HighlightReelProps) {
  const handleExport = () => {
    const data = highlights.map((h) => ({
      time: h.timestamp,
      time_seconds: h.timeSec,
      event_type: h.eventType,
      description: h.description,
      tension_score: h.tensionScore,
      confirmed: h.confirmed || false,
    }));
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `vio-highlights-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="max-w-2xl w-full max-h-[80vh] bg-brand-panel border border-brand-border rounded-2xl flex flex-col overflow-hidden shadow-2xl">
        {/* Header */}
        <div className="px-6 py-4 border-b border-brand-border flex items-center justify-between shrink-0">
          <div>
            <h2 className="text-lg font-bold text-white">Highlight Reel</h2>
            <p className="text-xs text-brand-muted mt-0.5">
              {highlights.length} key moment{highlights.length !== 1 ? "s" : ""} detected
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleExport}
              className="px-3 py-1.5 text-xs font-medium rounded-lg bg-brand-accent/10 text-brand-accent border border-brand-accent/30 hover:bg-brand-accent/20 transition-colors"
            >
              Export JSON
            </button>
            <button
              onClick={onClose}
              className="w-8 h-8 flex items-center justify-center rounded-lg text-brand-muted hover:text-white hover:bg-white/10 transition-colors"
            >
              X
            </button>
          </div>
        </div>

        {/* Highlight list */}
        <div className="flex-1 overflow-y-auto p-4 space-y-2">
          {highlights.length === 0 ? (
            <div className="text-center text-sm text-brand-muted py-12">
              No significant highlights detected in this session.
            </div>
          ) : (
            highlights.map((hl) => {
              const cat = categoryColor(hl.category);
              const tension = tensionBar(hl.tensionScore);
              return (
                <div
                  key={hl.id}
                  className={`p-3 rounded-lg border border-brand-border ${cat.bg} hover:border-brand-primary/30 transition-colors cursor-pointer group`}
                  onClick={() => onSeek(hl.timeSec)}
                >
                  <div className="flex items-start gap-3">
                    {/* Timestamp */}
                    <div className="w-14 shrink-0">
                      <span className="text-sm font-mono text-brand-primary group-hover:underline">
                        {hl.timestamp}
                      </span>
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <div className={`w-2 h-2 rounded-full ${cat.dot}`} />
                        <span className={`text-xs font-semibold ${cat.text}`}>
                          {formatEventType(hl.eventType)}
                        </span>
                        {hl.confirmed && (
                          <span className="text-[9px] px-1.5 py-0.5 rounded bg-green-500/10 text-green-400 border border-green-500/30">
                            Confirmed
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-brand-muted leading-relaxed truncate">
                        {hl.description}
                      </p>
                    </div>

                    {/* Tension */}
                    <div className="w-16 shrink-0 flex flex-col items-end gap-1">
                      <span className="text-[10px] text-brand-muted">Tension</span>
                      <div className="w-full h-1.5 bg-brand-border rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full ${tension.color}`}
                          style={{ width: tension.width }}
                        />
                      </div>
                      <span className="text-[10px] font-mono text-brand-muted">
                        {hl.tensionScore}/10
                      </span>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
