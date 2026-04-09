"use client";

import { useState } from "react";

const DEFAULT_URL =
  "https://firebasestorage.googleapis.com/v0/b/tipio-1ec97.appspot.com/o/bar.v.psg.1.ucl.01.10.2025.fullmatchsports.com.1080p.mp4?alt=media&token=593ce8a1-0462-4c37-98c3-e399f25e3853";

type Status = "idle" | "analyzing" | "paused";
export type AIMode = "cloud" | "local";

interface HeaderProps {
  status: Status;
  onStatusChange: (status: Status, url?: string) => void;
  sourceUrl: string;
  onSourceUrlChange: (url: string) => void;
  aiMode: AIMode;
  onAIModeChange: (mode: AIMode) => void;
  frontendVersion: string;
  backendVersion: string | null;
}

export function Header({
  status,
  onStatusChange,
  sourceUrl,
  onSourceUrlChange,
  aiMode,
  onAIModeChange,
  frontendVersion,
  backendVersion,
}: HeaderProps) {
  const [localUrl, setLocalUrl] = useState(sourceUrl || DEFAULT_URL);
  const isAnalyzing = status === "analyzing";

  const handleStart = () => {
    const trimmed = localUrl.trim();
    onSourceUrlChange(trimmed);
    onStatusChange("analyzing", trimmed);
  };

  const handlePause = () => {
    onStatusChange(status === "paused" ? "analyzing" : "paused");
  };

  const handleStop = () => {
    onStatusChange("idle");
  };

  return (
    <header className="glass-header h-16 flex items-center justify-between px-6 shrink-0 z-50">
      {/* Logo */}
      <div className="flex items-center gap-4 w-1/4">
        <div className="w-8 h-8 rounded bg-gradient-to-br from-brand-primary to-brand-accent flex items-center justify-center">
          <span className="text-white text-xs font-semibold">VV</span>
        </div>
        <h1 className="text-lg font-semibold tracking-tight">Vio Vision Demo</h1>
      </div>

      {/* URL + controls */}
      <div className="flex-1 max-w-2xl flex items-center gap-3">
        <div className="flex-1 relative">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <span className="text-brand-muted text-sm">URL</span>
          </div>
          <input
            type="text"
            value={localUrl}
            onChange={(e) => setLocalUrl(e.target.value)}
            placeholder="Match Source URL"
            className="w-full bg-brand-panel/50 border border-brand-border rounded-lg py-1.5 pl-10 pr-4 text-sm text-white placeholder-brand-muted focus:outline-none focus:border-brand-primary/50 transition-colors"
          />
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={handleStart}
            className="px-4 py-1.5 bg-brand-primary/10 text-brand-primary border border-brand-primary/20 rounded-lg text-sm font-medium hover:bg-brand-primary/20 transition-colors"
          >
            {isAnalyzing || status === "paused" ? "Restart" : "Start Analysis"}
          </button>
          {(isAnalyzing || status === "paused") && (
            <>
              <button
                onClick={handlePause}
                className="w-8 h-8 flex items-center justify-center bg-brand-panel border border-brand-border rounded-lg text-brand-muted hover:text-white transition-colors text-xs"
              >
                {status === "paused" ? "▶" : "II"}
              </button>
              <button
                onClick={handleStop}
                className="w-8 h-8 flex items-center justify-center bg-brand-panel border border-red-500/30 rounded-lg text-red-400 hover:bg-red-500/10 transition-colors text-xs"
                title="Stop analysis"
              >
                ■
              </button>
            </>
          )}
        </div>
      </div>

      {/* Right: status */}
      <div className="w-1/4 flex justify-end items-center gap-3">
        {/* Status pill */}
        <div className="flex items-center gap-2 px-3 py-1 rounded-full border text-xs font-medium border-brand-success/20 bg-brand-success/10 text-brand-success">
          <div className="w-2 h-2 rounded-full bg-brand-success animate-pulse" />
          <span>
            {status === "idle" ? "Idle" : status === "analyzing" ? "Analyzing" : "Paused"}
          </span>
        </div>

        {/* Version info */}
        <div className="flex flex-col items-end gap-0.5">
          <span className="text-[9px] font-mono text-brand-muted">v{frontendVersion}</span>
          {backendVersion && backendVersion !== frontendVersion && (
            <span className="text-[9px] font-mono text-red-400" title={`Frontend v${frontendVersion} / Backend v${backendVersion}`}>
              API mismatch v{backendVersion}
            </span>
          )}
        </div>
      </div>
    </header>
  );
}
