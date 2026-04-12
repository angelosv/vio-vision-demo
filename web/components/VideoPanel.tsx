"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import type { TrackedObject, Ball } from "@/types/events";

interface VideoPanelProps {
  currentTime: number;
  totalTime: number;
  onTimeChange: (t: number) => void;
  onSeek?: (time: number) => void;
  videoUrl?: string | null;
  tracks: TrackedObject[];
  ball: Ball | null;
  crowdIntensity?: number;
  sentiment?: string | null;
  isPlaying?: boolean;
  onPlayPause?: () => void;
  scoreboard?: { home_team?: string; away_team?: string; home_score: number; away_score: number } | null;
  sceneMarkers?: { start_sec: number }[];
  highlightMarkers?: { timeSec: number; category: string }[];
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60).toString().padStart(2, "0");
  const s = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function trackColor(t: TrackedObject): { border: string; bg: string; text: string } {
  if (t.type === "Referee") return { border: "#f472b6", bg: "#f472b6", text: "#000" };
  if (t.type === "Goalkeeper") return { border: "#facc15", bg: "#facc15", text: "#000" };
  if (t.team_color) return { border: t.team_color, bg: t.team_color, text: "#000" };
  if (t.team === "home") return { border: "#FE9330", bg: "#FE9330", text: "#000" };
  if (t.team === "away") return { border: "#2C7A94", bg: "#2C7A94", text: "#fff" };
  return { border: "#FE9330", bg: "#FE9330", text: "#000" };
}

function trackLabel(t: TrackedObject): string {
  if (t.type === "Referee") return "REF";
  if (t.jersey_number) return `#${t.jersey_number}`;
  return `T${t.track_id}`;
}

const SENTIMENT_CONFIG: Record<string, { dot: string; color: string; label: string }> = {
  calm: { dot: "bg-blue-400", color: "text-blue-400", label: "Calm" },
  tense: { dot: "bg-yellow-400", color: "text-yellow-400", label: "Tense" },
  euphoric: { dot: "bg-green-400", color: "text-green-400", label: "Euphoric" },
  frustrated: { dot: "bg-red-400", color: "text-red-400", label: "Frustrated" },
};

export function VideoPanel({
  currentTime,
  totalTime,
  onTimeChange,
  onSeek,
  videoUrl,
  tracks,
  ball,
  crowdIntensity = -1,
  sentiment,
  isPlaying = true,
  onPlayPause,
  scoreboard,
  sceneMarkers,
  highlightMarkers,
}: VideoPanelProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [muted, setMuted] = useState(true);
  const [videoDuration, setVideoDuration] = useState(0);

  const isNative = !!videoUrl;
  const effectiveDuration = isNative && videoDuration > 0 ? videoDuration : totalTime;
  const effectiveTime = isNative && videoRef.current ? videoRef.current.currentTime : currentTime;
  const progress = effectiveDuration > 0 ? (effectiveTime / effectiveDuration) * 100 : 0;

  const handleTimeUpdate = useCallback(() => {
    if (!videoRef.current) return;
    onTimeChange(videoRef.current.currentTime);
  }, [onTimeChange]);

  const handleMetadataLoaded = () => {
    if (videoRef.current) setVideoDuration(videoRef.current.duration);
  };

  useEffect(() => {
    if (!videoRef.current || !isNative) return;
    if (isPlaying) videoRef.current.play().catch(() => {});
    else videoRef.current.pause();
  }, [isPlaying, isNative]);

  useEffect(() => {
    if (videoRef.current) videoRef.current.muted = muted;
  }, [muted]);

  const handleTimelineClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    const targetTime = effectiveDuration * ratio;
    if (isNative && videoRef.current) videoRef.current.currentTime = targetTime;
    onTimeChange(targetTime);
    onSeek?.(targetTime);
  };

  const playerCount = tracks.filter((t) => t.type === "Player" || t.type === "Goalkeeper").length;
  const refCount = tracks.filter((t) => t.type === "Referee").length;

  return (
    <div className="flex-1 flex flex-col gap-4 min-w-0">
      <div className="relative flex-1 rounded-xl overflow-hidden glass-panel flex flex-col">
        <div className="flex-1 relative bg-black">
          {/* Video / Placeholder */}
          {isNative ? (
            <video
              ref={videoRef}
              src={videoUrl!}
              className="w-full h-full object-contain"
              muted={muted}
              playsInline
              preload="auto"
              onTimeUpdate={handleTimeUpdate}
              onLoadedMetadata={handleMetadataLoaded}
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-brand-muted text-sm">
              Waiting for video source…
            </div>
          )}

          {/* Tracked objects overlay */}
          {tracks.map((t) => {
            const color = trackColor(t);
            const { x1, y1, x2, y2 } = t.bbox;
            return (
              <div
                key={t.track_id}
                className="absolute pointer-events-none transition-all duration-150 ease-linear"
                style={{
                  left: `${x1 * 100}%`,
                  top: `${y1 * 100}%`,
                  width: `${(x2 - x1) * 100}%`,
                  height: `${(y2 - y1) * 100}%`,
                  border: `2px solid ${color.border}`,
                  borderRadius: "3px",
                  boxShadow: `0 0 6px ${color.border}66`,
                }}
              >
                <span
                  className="absolute -top-5 left-0 text-[9px] font-bold px-1 py-0.5 rounded-sm whitespace-nowrap"
                  style={{ background: color.bg, color: color.text }}
                >
                  {trackLabel(t)}
                </span>
              </div>
            );
          })}

          {/* Ball */}
          {ball && (
            <div
              className="absolute pointer-events-none transition-all duration-150 ease-linear"
              style={{
                left: `${ball.bbox.x1 * 100}%`,
                top: `${ball.bbox.y1 * 100}%`,
                width: `${(ball.bbox.x2 - ball.bbox.x1) * 100}%`,
                height: `${(ball.bbox.y2 - ball.bbox.y1) * 100}%`,
                border: "2px solid #ffffff",
                borderRadius: "50%",
                boxShadow: "0 0 8px white",
              }}
            />
          )}

          {/* Scoreboard overlay */}
          {scoreboard && (
            <div className="absolute top-4 right-4 bg-black/70 backdrop-blur border border-white/10 rounded-lg px-4 py-2 flex items-center gap-3 z-10">
              <span className="text-sm font-semibold">{scoreboard.home_team ?? "Home"}</span>
              <span className="text-lg font-bold text-brand-primary">{scoreboard.home_score}</span>
              <span className="text-xs text-brand-muted">-</span>
              <span className="text-lg font-bold text-brand-accent">{scoreboard.away_score}</span>
              <span className="text-sm font-semibold">{scoreboard.away_team ?? "Away"}</span>
            </div>
          )}

          {/* Metrics overlay — stays ONLY for live system health, not events */}
          <div className="absolute top-4 left-4 flex gap-2 flex-wrap">
            <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
              <span className="text-brand-success text-xs">Players</span>
              <span className="text-xs font-mono">{playerCount}</span>
            </div>
            {refCount > 0 && (
              <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
                <span className="text-pink-400 text-xs">Refs</span>
                <span className="text-xs font-mono">{refCount}</span>
              </div>
            )}
            <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
              <span className="text-brand-accent text-xs">Ball</span>
              <span className="text-xs font-mono">{ball ? "Y" : "-"}</span>
            </div>
            {sentiment && SENTIMENT_CONFIG[sentiment] && (
              <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
                <div className={`w-2 h-2 rounded-full ${SENTIMENT_CONFIG[sentiment].dot} animate-pulse`} />
                <span className={`text-xs font-medium ${SENTIMENT_CONFIG[sentiment].color}`}>
                  {SENTIMENT_CONFIG[sentiment].label}
                </span>
              </div>
            )}
            {crowdIntensity >= 0 && (
              <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
                <span
                  className={`text-xs ${
                    crowdIntensity >= 7 ? "text-red-400" :
                    crowdIntensity >= 4 ? "text-yellow-400" : "text-brand-muted"
                  }`}
                >
                  Crowd
                </span>
                <div className="flex items-center gap-1">
                  <div className="w-12 h-1.5 bg-brand-border rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-300 ${
                        crowdIntensity >= 7 ? "bg-red-400" :
                        crowdIntensity >= 4 ? "bg-yellow-400" : "bg-brand-muted"
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
            onClick={handleTimelineClick}
          >
            <div className="absolute w-full h-1.5 bg-brand-border rounded-full overflow-hidden">
              <div className="h-full bg-brand-primary" style={{ width: `${progress}%` }} />
            </div>
            {sceneMarkers?.map((scene, i) => (
              <div
                key={i}
                className="absolute top-0 w-0.5 h-full bg-brand-accent/40"
                style={{ left: `${(scene.start_sec / effectiveDuration) * 100}%` }}
                title={`Scene at ${formatTime(scene.start_sec)}`}
              />
            ))}
            {highlightMarkers?.map((hl, i) => {
              const c =
                hl.category === "critical" ? "#ef4444" :
                hl.category === "card" ? "#eab308" :
                hl.category === "foul" ? "#f97316" : "#38bdf8";
              return (
                <div
                  key={`hl-${i}`}
                  className="absolute top-0 h-full flex items-center z-[5]"
                  style={{ left: `${(hl.timeSec / effectiveDuration) * 100}%` }}
                  title={`Highlight at ${formatTime(hl.timeSec)}`}
                >
                  <div
                    className="w-2 h-2 rotate-45"
                    style={{ backgroundColor: c, boxShadow: `0 0 4px ${c}` }}
                  />
                </div>
              );
            })}
            <div
              className="absolute w-3 h-3 rounded-full bg-white shadow-[0_0_8px_#FFF] -mt-0.5 border-2 border-black z-10"
              style={{ left: `${progress}%` }}
            />
          </div>

          <div className="flex justify-between items-center text-xs font-mono text-brand-muted">
            <div className="flex items-center gap-4">
              <button className="hover:text-white w-6 h-6 flex items-center justify-center" onClick={onPlayPause}>
                {isPlaying ? (
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor"><rect x="1" y="1" width="3" height="10" rx="1"/><rect x="8" y="1" width="3" height="10" rx="1"/></svg>
                ) : (
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor"><path d="M2 1l9 5-9 5V1z"/></svg>
                )}
              </button>
              <button className="hover:text-white w-6 h-6 flex items-center justify-center" onClick={() => setMuted((m) => !m)}>
                {muted ? (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 5L6 9H2v6h4l5 4V5z"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>
                ) : (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M19.07 4.93a10 10 0 010 14.14M15.54 8.46a5 5 0 010 7.07"/></svg>
                )}
              </button>
              <span>{formatTime(effectiveTime)} / {formatTime(effectiveDuration)}</span>
            </div>
            <div className="flex items-center gap-3">
              <button className="hover:text-white px-2 py-1 rounded bg-white/5 border border-white/10">1x</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
