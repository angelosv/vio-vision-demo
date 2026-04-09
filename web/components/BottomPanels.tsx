"use client";

import { useState } from "react";
import type { Detection, TensionPoint } from "@/types/events";

interface BottomPanelsProps {
  detections: Detection[];
  tensionHistory: TensionPoint[];
  teamColors?: string[];
}

export function BottomPanels({ detections, tensionHistory, teamColors = [] }: BottomPanelsProps) {
  const [pitchOpen, setPitchOpen] = useState(true);
  const [momentumOpen, setMomentumOpen] = useState(true);

  // ── Pitch Control data ──
  const players = detections.filter((d) => d.label === "Player" || d.label === "GK");
  const ball = detections.find((d) => d.label === "Ball");
  const leftHalf = players.filter((d) => centerX(d) < 0.5).length;
  const rightHalf = players.length - leftHalf;
  const leftPct = players.length > 0 ? Math.round((leftHalf / players.length) * 100) : 50;

  return (
    <div className="shrink-0 flex gap-4">
      {/* ── Pitch Control ── */}
      <div className="flex-1 glass-panel rounded-xl overflow-hidden flex flex-col transition-all duration-300">
        <button
          onClick={() => setPitchOpen((v) => !v)}
          className="w-full p-3 flex items-center justify-between bg-brand-panel/50 border-b border-brand-border hover:bg-brand-panel transition-colors"
        >
          <h3 className="text-xs font-semibold text-brand-muted uppercase tracking-wider flex items-center gap-2">
            <span className="text-brand-primary">■</span> Pitch Control
          </h3>
          <span className={`text-brand-muted text-xs transition-transform duration-300 ${pitchOpen ? "rotate-0" : "rotate-180"}`}>˄</span>
        </button>
        {pitchOpen && (
          <div className="p-4 flex-1 flex flex-col gap-2">
            {/* Mini pitch */}
            <div className="w-full h-28 relative border border-white/10 rounded bg-[#1a2e1a] overflow-hidden">
              {/* Center line + circle */}
              <div className="absolute left-1/2 top-0 bottom-0 w-px bg-white/20" />
              <div className="absolute top-1/2 left-1/2 w-10 h-10 -mt-5 -ml-5 border border-white/15 rounded-full" />
              {/* Penalty areas */}
              <div className="absolute top-1/4 left-0 w-[12%] h-1/2 border-r border-t border-b border-white/15 rounded-r" />
              <div className="absolute top-1/4 right-0 w-[12%] h-1/2 border-l border-t border-b border-white/15 rounded-l" />

              {/* Player dots — coloured by team */}
              {players.map((p, i) => {
                const dotColor = p.team !== undefined && teamColors[p.team]
                  ? teamColors[p.team]
                  : "#FE9330";
                return (
                  <div
                    key={i}
                    className="absolute w-2.5 h-2.5 rounded-full border"
                    style={{
                      left: `${centerX(p) * 100}%`,
                      top: `${centerY(p) * 100}%`,
                      transform: "translate(-50%,-50%)",
                      backgroundColor: dotColor,
                      borderColor: dotColor,
                      boxShadow: `0 0 4px ${dotColor}88`,
                    }}
                  />
                );
              })}

              {/* Ball */}
              {ball && (
                <div
                  className="absolute w-3 h-3 rounded-full bg-white shadow-[0_0_8px_white] border border-white/50"
                  style={{ left: `${centerX(ball) * 100}%`, top: `${centerY(ball) * 100}%`, transform: "translate(-50%,-50%)" }}
                />
              )}

              {/* No data overlay */}
              {players.length === 0 && (
                <div className="absolute inset-0 flex items-center justify-center text-xs text-brand-muted">
                  Waiting for detections...
                </div>
              )}
            </div>

            {/* Possession bar */}
            <div className="flex items-center gap-2 text-[10px]">
              <span className="text-blue-400 font-mono w-8 text-right">{leftPct}%</span>
              <div className="flex-1 h-1.5 bg-brand-border rounded-full overflow-hidden flex">
                <div className="h-full bg-blue-400 transition-all duration-500" style={{ width: `${leftPct}%` }} />
                <div className="h-full bg-white/40 transition-all duration-500" style={{ width: `${100 - leftPct}%` }} />
              </div>
              <span className="text-white/60 font-mono w-8">{100 - leftPct}%</span>
            </div>
            <div className="flex justify-between text-[10px] text-brand-muted px-10">
              <span>Left ({leftHalf})</span>
              <span>Right ({rightHalf})</span>
            </div>
          </div>
        )}
      </div>

      {/* ── Team Momentum ── */}
      <div className="flex-1 glass-panel rounded-xl overflow-hidden flex flex-col transition-all duration-300">
        <button
          onClick={() => setMomentumOpen((v) => !v)}
          className="w-full p-3 flex items-center justify-between bg-brand-panel/50 border-b border-brand-border hover:bg-brand-panel transition-colors"
        >
          <h3 className="text-xs font-semibold text-brand-muted uppercase tracking-wider flex items-center gap-2">
            <span className="text-brand-accent">■</span> Match Tension
          </h3>
          <span className={`text-brand-muted text-xs transition-transform duration-300 ${momentumOpen ? "rotate-0" : "rotate-180"}`}>˄</span>
        </button>
        {momentumOpen && (
          <div className="p-4 flex-1 flex flex-col gap-1">
            {tensionHistory.length > 1 ? (
              <MomentumChart data={tensionHistory} />
            ) : (
              <div className="flex-1 flex items-center justify-center text-xs text-brand-muted">
                Tension data will appear as analysis progresses...
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Helpers ──

function centerX(d: Detection): number {
  return (d.box[0] + d.box[2]) / 2;
}
function centerY(d: Detection): number {
  return (d.box[1] + d.box[3]) / 2;
}

// ── Momentum Chart (SVG) ──

function MomentumChart({ data }: { data: TensionPoint[] }) {
  const W = 400;
  const H = 100;
  const PAD = 2;

  if (data.length < 2) return null;

  const minT = data[0].time;
  const maxT = data[data.length - 1].time;
  const rangeT = maxT - minT || 1;

  const toX = (t: number) => PAD + ((t - minT) / rangeT) * (W - PAD * 2);
  const toY = (s: number) => H - PAD - (s / 10) * (H - PAD * 2);

  const points = data.map((d) => `${toX(d.time)},${toY(d.score)}`).join(" ");
  const areaPoints = `${toX(data[0].time)},${H - PAD} ${points} ${toX(data[data.length - 1].time)},${H - PAD}`;

  // Current tension
  const current = data[data.length - 1].score;
  const avg = data.reduce((s, d) => s + d.score, 0) / data.length;

  return (
    <div className="flex flex-col gap-1 flex-1">
      <div className="flex items-center gap-3 text-[10px]">
        <span className="text-brand-muted">Current:</span>
        <span className={`font-mono font-bold ${current >= 7 ? "text-red-400" : current >= 4 ? "text-yellow-400" : "text-brand-success"}`}>
          {current.toFixed(1)}
        </span>
        <span className="text-brand-muted ml-2">Avg:</span>
        <span className="font-mono text-brand-muted">{avg.toFixed(1)}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-24 flex-1" preserveAspectRatio="none">
        {/* Grid lines at 3, 5, 8 */}
        {[3, 5, 8].map((v) => (
          <line key={v} x1={PAD} y1={toY(v)} x2={W - PAD} y2={toY(v)} stroke="white" strokeOpacity={0.07} strokeDasharray="4 4" />
        ))}
        {/* Danger zone */}
        <rect x={PAD} y={toY(10)} width={W - PAD * 2} height={toY(8) - toY(10)} fill="red" fillOpacity={0.05} />
        {/* Area fill */}
        <polygon points={areaPoints} fill="url(#tensionGrad)" fillOpacity={0.3} />
        {/* Line */}
        <polyline points={points} fill="none" stroke="url(#tensionLine)" strokeWidth={2} strokeLinejoin="round" />
        {/* Current dot */}
        <circle cx={toX(data[data.length - 1].time)} cy={toY(current)} r={3}
          fill={current >= 7 ? "#f87171" : current >= 4 ? "#facc15" : "#38B2AC"}
          className="animate-pulse" />
        <defs>
          <linearGradient id="tensionGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#f87171" stopOpacity={0.6} />
            <stop offset="50%" stopColor="#facc15" stopOpacity={0.3} />
            <stop offset="100%" stopColor="#38B2AC" stopOpacity={0.1} />
          </linearGradient>
          <linearGradient id="tensionLine" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#f87171" />
            <stop offset="50%" stopColor="#facc15" />
            <stop offset="100%" stopColor="#38B2AC" />
          </linearGradient>
        </defs>
      </svg>
    </div>
  );
}
