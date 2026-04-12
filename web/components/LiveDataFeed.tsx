"use client";

import { useEffect, useRef, useState } from "react";

interface LiveDataFeedProps {
  messages: any[];
  maxShown?: number;
}

/**
 * Scrollable panel showing the latest raw JSON messages coming over the
 * WebSocket. Great demo wow-factor to show Viaplay/TV2 the data feed is real.
 */
export function LiveDataFeed({ messages, maxShown = 50 }: LiveDataFeedProps) {
  const [autoScroll, setAutoScroll] = useState(true);
  const [paused, setPaused] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const shown = paused ? messages : messages.slice(-maxShown);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [shown.length, autoScroll]);

  return (
    <aside className="w-96 glass-panel rounded-xl flex flex-col overflow-hidden shrink-0">
      <div className="p-3 border-b border-brand-border bg-brand-panel/50">
        <div className="flex justify-between items-center mb-2">
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-brand-success animate-pulse" />
            Live Data Feed
            <span className="text-[10px] text-brand-muted font-normal">
              ({messages.length})
            </span>
          </h2>
          <div className="flex gap-1">
            <button
              onClick={() => setPaused((v) => !v)}
              className={`px-2 py-1 rounded text-[10px] ${
                paused ? "bg-yellow-500/20 text-yellow-400" : "bg-white/5 text-brand-muted hover:bg-white/10"
              }`}
              title={paused ? "Resume" : "Pause"}
            >
              {paused ? "Resume" : "Pause"}
            </button>
            <button
              onClick={() => setAutoScroll((v) => !v)}
              className={`px-2 py-1 rounded text-[10px] ${
                autoScroll
                  ? "bg-brand-primary/20 text-brand-primary"
                  : "bg-white/5 text-brand-muted hover:bg-white/10"
              }`}
              title="Auto-scroll"
            >
              Auto
            </button>
          </div>
        </div>
        <p className="text-[10px] text-brand-muted">
          Raw JSON from vio-gateway WebSocket. This is what Viaplay/TV2 receive.
        </p>
      </div>

      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-2 font-mono text-[10px] space-y-1.5"
      >
        {shown.length === 0 ? (
          <div className="text-center text-brand-muted py-8">
            Waiting for data...
          </div>
        ) : (
          shown.map((msg, i) => <FeedMessage key={i} msg={msg} />)
        )}
      </div>
    </aside>
  );
}

function FeedMessage({ msg }: { msg: any }) {
  const type = msg?.type ?? "?";
  const ts = msg?.time_sec ?? msg?.frame_time_sec;
  const evt = msg?.event_type ?? msg?.event?.type;

  const color =
    type === "metadata"
      ? "border-brand-primary/40 bg-brand-primary/5"
      : evt && evt !== "normal_play"
        ? "border-brand-success/40 bg-brand-success/5"
        : "border-white/10 bg-white/[0.02]";

  return (
    <div className={`rounded border ${color} p-1.5`}>
      <div className="flex justify-between items-center mb-0.5">
        <span className="text-brand-primary font-semibold">{type}</span>
        {ts !== undefined && (
          <span className="text-brand-muted">t={typeof ts === "number" ? ts.toFixed(1) : ts}s</span>
        )}
      </div>
      {evt && evt !== "normal_play" && (
        <div className="text-brand-success mb-0.5">event: {evt}</div>
      )}
      <div className="text-brand-muted text-[9px] break-all line-clamp-2 leading-tight">
        {truncate(JSON.stringify(msg), 180)}
      </div>
    </div>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}
