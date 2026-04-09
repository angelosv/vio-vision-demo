"use client";

import { useState, useEffect } from "react";

interface Detection {
  label: string;
  confidence: number;
  box: [number, number, number, number]; // [x1, y1, x2, y2] normalized 0–1
}

interface VideoPanelProps {
  currentTime: number;
  totalTime: number;
  onTimeChange: (t: number) => void;
  onSeek?: (time: number) => void;
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60).toString().padStart(2, "0");
  const s = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

// Color per label
function labelColor(label: string): { border: string; bg: string; text: string } {
  const l = label.toLowerCase();
  if (l === "ball")   return { border: "#ffffff", bg: "#ffffff", text: "#000" };
  if (l === "gk")     return { border: "#facc15", bg: "#facc15", text: "#000" };
  return                     { border: "#FE9330", bg: "#FE9330", text: "#000" };
}

export function VideoPanel({ currentTime, totalTime, onTimeChange, onSeek }: VideoPanelProps) {
  const [frameSrc, setFrameSrc] = useState(
    "https://images.unsplash.com/photo-1518605368461-1e129623b1bf?auto=format&fit=crop&q=80&w=2000"
  );
  const [detections, setDetections] = useState<Detection[]>([]);
  const [crowdIntensity, setCrowdIntensity] = useState(-1);
  const progress = totalTime > 0 ? (currentTime / totalTime) * 100 : 0;

  useEffect(() => {
    const handleUpdate = (e: any) => {
      const { frame, dets, crowdIntensity: ci } = e.detail;
      if (frame) setFrameSrc(frame);
      if (dets)  setDetections(dets);
      if (ci !== undefined) setCrowdIntensity(ci);
    };
    window.addEventListener("vio-frame-update", handleUpdate);
    return () => window.removeEventListener("vio-frame-update", handleUpdate);
  }, []);

  const handleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    const targetTime = totalTime * ratio;
    onTimeChange(targetTime);
    onSeek?.(targetTime);
  };

  return (
    <div className="flex-1 flex flex-col gap-4 min-w-0">
      <div className="relative flex-1 rounded-xl overflow-hidden glass-panel flex flex-col">
        <div className="flex-1 relative bg-black">

          {/* Frame */}
          <img
            src={frameSrc}
            alt="Football Match"
            className="w-full h-full object-contain"
          />

          {/* Real YOLO detections */}
          {detections.map((det, i) => {
            const [x1, y1, x2, y2] = det.box;
            const color = labelColor(det.label);
            return (
              <div
                key={i}
                className="absolute pointer-events-none"
                style={{
                  left:   `${x1 * 100}%`,
                  top:    `${y1 * 100}%`,
                  width:  `${(x2 - x1) * 100}%`,
                  height: `${(y2 - y1) * 100}%`,
                  border: `2px solid ${color.border}`,
                  borderRadius: "3px",
                  boxShadow: `0 0 8px ${color.border}88`,
                }}
              >
                <span
                  className="absolute -top-5 left-0 text-[9px] font-bold px-1 py-0.5 rounded-sm whitespace-nowrap"
                  style={{ background: color.bg, color: color.text }}
                >
                  {det.label} {Math.round(det.confidence * 100)}%
                </span>
              </div>
            );
          })}

          {/* Metrics overlay */}
          <div className="absolute top-4 left-4 flex gap-2">
            <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
              <span className="text-brand-primary text-xs">Objects</span>
              <span className="text-xs font-mono">{detections.length}</span>
            </div>
            <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
              <span className="text-brand-success text-xs">Players</span>
              <span className="text-xs font-mono">
                {detections.filter((d) => d.label === "Player" || d.label === "GK").length}
              </span>
            </div>
            <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
              <span className="text-brand-accent text-xs">Ball</span>
              <span className="text-xs font-mono">
                {detections.some((d) => d.label === "Ball") ? "✓" : "—"}
              </span>
            </div>
            {crowdIntensity >= 0 && (
              <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
                <span className={`text-xs ${crowdIntensity >= 7 ? "text-red-400" : crowdIntensity >= 4 ? "text-yellow-400" : "text-brand-muted"}`}>
                  Crowd
                </span>
                <div className="flex items-center gap-1">
                  <div className="w-12 h-1.5 bg-brand-border rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-300 ${
                        crowdIntensity >= 7 ? "bg-red-400" : crowdIntensity >= 4 ? "bg-yellow-400" : "bg-brand-muted"
                      }`}
                      style={{ width: `${crowdIntensity * 10}%` }}
                    />
                  </div>
                  <span className="text-xs font-mono w-6 text-right">
                    {crowdIntensity.toFixed(0)}
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Timeline */}
        <div className="h-20 bg-brand-panel/80 border-t border-brand-border px-4 py-2 flex flex-col justify-center gap-2 shrink-0">
          <div
            className="relative h-8 flex items-center group cursor-pointer"
            onClick={handleClick}
          >
            <div className="absolute w-full h-1.5 bg-brand-border rounded-full overflow-hidden">
              <div className="h-full bg-brand-primary" style={{ width: `${progress}%` }} />
            </div>
            <div
              className="absolute w-3 h-3 rounded-full bg-white shadow-[0_0_8px_#FFF] -mt-0.5 border-2 border-black z-10"
              style={{ left: `${progress}%` }}
            />
          </div>

          <div className="flex justify-between items-center text-xs font-mono text-brand-muted">
            <div className="flex items-center gap-4">
              <button className="hover:text-white">▶</button>
              <button className="hover:text-white">🔊</button>
              <span>
                {formatTime(currentTime)} / {formatTime(totalTime)}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <button className="hover:text-white px-2 py-1 rounded bg-white/5 border border-white/10">
                1x
              </button>
              <button className="hover:text-white">⛶</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
