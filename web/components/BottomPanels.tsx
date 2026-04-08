"use client";

import { useState } from "react";

export function BottomPanels() {
  const [pitchOpen, setPitchOpen] = useState(true);
  const [momentumOpen, setMomentumOpen] = useState(true);

  return (
    <div className="shrink-0 flex gap-4">
      <div className="flex-1 glass-panel rounded-xl overflow-hidden flex flex-col transition-all duration-300">
        <button
          onClick={() => setPitchOpen((v) => !v)}
          className="w-full p-3 flex items-center justify-between bg-brand-panel/50 border-b border-brand-border hover:bg-brand-panel transition-colors"
        >
          <h3 className="text-xs font-semibold text-brand-muted uppercase tracking-wider flex items-center gap-2">
            <span className="text-brand-primary">■</span> Pitch Control
          </h3>
          <span
            className={`text-brand-muted text-xs transition-transform duration-300 ${
              pitchOpen ? "rotate-0" : "rotate-180"
            }`}
          >
            ˄
          </span>
        </button>
        {pitchOpen && (
          <div className="p-4 flex-1 flex items-center justify-center">
            <div className="w-full max-w-sm h-full relative border border-white/10 rounded bg-[#1a2e1a] opacity-80">
              <div className="absolute inset-0 flex">
                <div className="w-1/2 h-full border-r border-white/20" />
              </div>
              <div className="absolute inset-y-0 left-0 w-1/4 bg-blue-500/20" />
              <div className="absolute inset-y-0 right-0 w-3/4 bg-white/10" />
              <div className="absolute top-1/2 left-1/2 w-8 h-8 -mt-4 -ml-4 border border-white/20 rounded-full" />
            </div>
          </div>
        )}
      </div>

      <div className="flex-1 glass-panel rounded-xl overflow-hidden flex flex-col transition-all duration-300">
        <button
          onClick={() => setMomentumOpen((v) => !v)}
          className="w-full p-3 flex items-center justify-between bg-brand-panel/50 border-b border-brand-border hover:bg-brand-panel transition-colors"
        >
          <h3 className="text-xs font-semibold text-brand-muted uppercase tracking-wider flex items-center gap-2">
            <span className="text-brand-accent">■</span> Team Momentum
          </h3>
          <span
            className={`text-brand-muted text-xs transition-transform duration-300 ${
              momentumOpen ? "rotate-0" : "rotate-180"
            }`}
          >
            ˄
          </span>
        </button>
        {momentumOpen && (
          <div className="p-4 flex-1 flex items-center justify-center">
            <div className="w-full h-32 border border-white/10 rounded bg-brand-panel/70 flex items-center justify-center text-xs text-brand-muted">
              Momentum chart placeholder
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
