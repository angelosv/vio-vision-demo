"use client";

import type { MatchState } from "@/types/events";

interface MatchContextPanelProps {
  state: MatchState | null;
  teamColors?: string[];
}

export function MatchContextPanel({ state, teamColors = [] }: MatchContextPanelProps) {
  if (!state) {
    return (
      <div className="glass-panel rounded-xl p-3 text-xs text-brand-muted">
        Match context will appear as analysis progresses…
      </div>
    );
  }

  const home = state.teams.home;
  const away = state.teams.away;
  const scoreDisplay = state.score
    ? `${state.score.home} - ${state.score.away}`
    : null;

  const homeColor = teamColors[0] || "#FE9330";
  const awayColor = teamColors[1] || "#2C7A94";

  return (
    <div className="glass-panel rounded-xl p-3 flex flex-col gap-3">
      {/* Header: score + minute */}
      <div className="flex items-center justify-between">
        <div className="flex items-baseline gap-3">
          <h3 className="text-xs font-semibold text-brand-muted uppercase tracking-wider">
            Match Context
          </h3>
          {state.minute !== null && (
            <span className="text-[10px] font-mono text-brand-muted">
              min {state.minute}'
            </span>
          )}
        </div>
        {scoreDisplay && (
          <div className="font-mono text-sm font-bold">
            <span style={{ color: homeColor }}>{state.score!.home}</span>
            <span className="text-brand-muted mx-1">-</span>
            <span style={{ color: awayColor }}>{state.score!.away}</span>
          </div>
        )}
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-3 gap-x-3 gap-y-1 text-[11px]">
        <div className="text-right font-mono text-white">
          {stat(home.shots)}
        </div>
        <div className="text-center text-[9px] text-brand-muted uppercase tracking-wider">
          shots
        </div>
        <div className="text-left font-mono text-white">{stat(away.shots)}</div>

        <div className="text-right font-mono text-white">
          {stat(home.corners)}
        </div>
        <div className="text-center text-[9px] text-brand-muted uppercase tracking-wider">
          corners
        </div>
        <div className="text-left font-mono text-white">{stat(away.corners)}</div>

        <div className="text-right font-mono text-white">
          {stat(home.fouls)}
        </div>
        <div className="text-center text-[9px] text-brand-muted uppercase tracking-wider">
          fouls
        </div>
        <div className="text-left font-mono text-white">{stat(away.fouls)}</div>

        <div className="text-right font-mono">
          <CardBadge y={home.yellow_cards} r={home.red_cards} />
        </div>
        <div className="text-center text-[9px] text-brand-muted uppercase tracking-wider">
          cards
        </div>
        <div className="text-left font-mono">
          <CardBadge y={away.yellow_cards} r={away.red_cards} />
        </div>
      </div>

      {/* Momentum */}
      <div className="flex flex-col gap-1 text-[10px]">
        <div className="flex justify-between text-brand-muted uppercase tracking-wider">
          <span>Momentum</span>
          <span>({state.momentum.window_sec.toFixed(0)}s window)</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="font-mono w-8 text-right" style={{ color: homeColor }}>
            {state.momentum.home_percent.toFixed(0)}%
          </span>
          <div className="flex-1 h-1.5 bg-brand-border rounded-full overflow-hidden flex">
            <div
              className="h-full transition-all duration-500"
              style={{
                width: `${state.momentum.home_percent}%`,
                backgroundColor: homeColor,
              }}
            />
            <div
              className="h-full transition-all duration-500"
              style={{
                width: `${state.momentum.away_percent}%`,
                backgroundColor: awayColor,
              }}
            />
          </div>
          <span className="font-mono w-8" style={{ color: awayColor }}>
            {state.momentum.away_percent.toFixed(0)}%
          </span>
        </div>
        <div className="text-center text-[9px]">
          <MomentumLabel
            direction={state.momentum.direction}
            homeColor={homeColor}
            awayColor={awayColor}
          />
        </div>
      </div>

      {/* Flagged players */}
      {state.players_flagged.length > 0 && (
        <div className="flex flex-col gap-1 text-[10px] border-t border-brand-border pt-2">
          <span className="text-brand-muted uppercase tracking-wider">
            Watch list
          </span>
          {state.players_flagged.slice(0, 3).map((p) => (
            <div
              key={p.track_id}
              className="flex justify-between items-center"
            >
              <span>
                <span
                  className="font-mono font-bold"
                  style={{
                    color: p.team === "home" ? homeColor : awayColor,
                  }}
                >
                  {p.jersey ? `#${p.jersey}` : `T-${p.track_id}`}
                </span>
                <span className="text-brand-muted ml-1">({p.team})</span>
              </span>
              <span className="font-mono text-[9px]">
                {p.yellow_cards > 0 && (
                  <span className="text-yellow-400 mr-2">
                    {p.yellow_cards}Y
                  </span>
                )}
                {p.fouls > 0 && (
                  <span className="text-orange-400">{p.fouls} fouls</span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function stat(n: number): string {
  return n > 0 ? String(n) : "—";
}

function CardBadge({ y, r }: { y: number; r: number }) {
  if (y === 0 && r === 0) return <span className="text-brand-muted">—</span>;
  return (
    <span>
      {y > 0 && <span className="text-yellow-400">{y}Y</span>}
      {y > 0 && r > 0 && <span> </span>}
      {r > 0 && <span className="text-red-400">{r}R</span>}
    </span>
  );
}

function MomentumLabel({
  direction,
  homeColor,
  awayColor,
}: {
  direction: "home_dominant" | "away_dominant" | "balanced";
  homeColor: string;
  awayColor: string;
}) {
  if (direction === "home_dominant") {
    return <span style={{ color: homeColor }}>← Home pressing</span>;
  }
  if (direction === "away_dominant") {
    return <span style={{ color: awayColor }}>Away pressing →</span>;
  }
  return <span className="text-brand-muted">Balanced</span>;
}
