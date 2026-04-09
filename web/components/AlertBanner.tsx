"use client";

import { useEffect, useState } from "react";
import type { MatchEvent } from "@/types/events";

interface AlertBannerProps {
  event: MatchEvent | null;
  onDismiss: () => void;
}

export function AlertBanner({ event, onDismiss }: AlertBannerProps) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (event) {
      setVisible(true);
      const timer = setTimeout(() => {
        setVisible(false);
        setTimeout(onDismiss, 300); // wait for exit animation
      }, 5000);
      return () => clearTimeout(timer);
    } else {
      setVisible(false);
    }
  }, [event, onDismiss]);

  if (!event) return null;

  return (
    <div
      className={`fixed top-20 left-1/2 -translate-x-1/2 z-50 transition-all duration-300 ${
        visible ? "opacity-100 translate-y-0" : "opacity-0 -translate-y-4"
      }`}
    >
      <div className="flex items-center gap-3 px-5 py-3 rounded-xl border border-red-500/40 bg-red-950/80 backdrop-blur-lg shadow-[0_0_30px_rgba(239,68,68,0.3)]">
        <div className="w-2 h-2 rounded-full bg-red-400 animate-pulse" />
        <div>
          <div className="flex items-center gap-2">
            <span className="text-red-400 text-xs font-mono">{event.timestamp}</span>
            <span className="text-sm font-semibold text-white">{event.title}</span>
            {event.tensionScore !== undefined && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-300 border border-red-500/30">
                Tension {event.tensionScore}/10
              </span>
            )}
          </div>
          {event.description && (
            <p className="text-xs text-red-200/70 mt-0.5 max-w-md">{event.description}</p>
          )}
        </div>
        <button
          onClick={() => { setVisible(false); setTimeout(onDismiss, 300); }}
          className="ml-2 text-red-400/60 hover:text-red-300 text-xs"
        >
          x
        </button>
      </div>
    </div>
  );
}
