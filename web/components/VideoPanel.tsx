"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import type { Detection } from "@/types/events";

export interface DetectionFrame {
  detections: Detection[];
  teamColors: string[];
  crowdIntensity: number;
}

interface VideoPanelProps {
  currentTime: number;
  totalTime: number;
  onTimeChange: (t: number) => void;
  onSeek?: (time: number) => void;
  videoUrl?: string | null;
  detectionBuffer?: Map<number, DetectionFrame>;
  isPlaying?: boolean;
  onPlayPause?: () => void;
  scoreboard?: { home_team?: string; away_team?: string; home_score: number; away_score: number } | null;
  sceneMarkers?: { start_sec: number }[];
  highlightMarkers?: { timeSec: number; category: string }[];
  sentiment?: string | null; // "calm" | "tense" | "euphoric" | "frustrated"
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60).toString().padStart(2, "0");
  const s = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function labelColor(det: Detection): { border: string; bg: string; text: string } {
  const l = det.label.toLowerCase();
  if (l === "ball") return { border: "#ffffff", bg: "#ffffff", text: "#000" };
  if (l === "gk") return { border: "#facc15", bg: "#facc15", text: "#000" };
  if (l === "referee") return { border: "#f472b6", bg: "#f472b6", text: "#000" };
  if (l === "crowd") return { border: "#6b7280", bg: "#6b7280", text: "#fff" };
  // Use team color if available
  if (det.color) return { border: det.color, bg: det.color, text: "#000" };
  if (det.team === 0) return { border: "#FE9330", bg: "#FE9330", text: "#000" };
  if (det.team === 1) return { border: "#2C7A94", bg: "#2C7A94", text: "#fff" };
  return { border: "#FE9330", bg: "#FE9330", text: "#000" };
}

function detectionLabel(det: Detection): string {
  if (det.label === "Crowd") return "";
  const base = det.label;
  if (det.player_id && det.label !== "Ball") {
    return `#${det.player_id} ${base}`;
  }
  return base;
}

const SENTIMENT_CONFIG: Record<string, { emoji: string; color: string; label: string }> = {
  calm: { emoji: "😌", color: "text-blue-400", label: "Calm" },
  tense: { emoji: "😰", color: "text-yellow-400", label: "Tense" },
  euphoric: { emoji: "🤩", color: "text-green-400", label: "Euphoric" },
  frustrated: { emoji: "😤", color: "text-red-400", label: "Frustrated" },
};

/** Find nearest detection frame within tolerance. */
function findNearest(
  buffer: Map<number, DetectionFrame>,
  target: number,
  tolerance: number,
): DetectionFrame | null {
  let best: DetectionFrame | null = null;
  let bestDist = tolerance;
  for (const [key, value] of buffer) {
    const dist = Math.abs(key - target);
    if (dist < bestDist) {
      bestDist = dist;
      best = value;
    }
  }
  return best;
}

export function VideoPanel({
  currentTime,
  totalTime,
  onTimeChange,
  onSeek,
  videoUrl,
  detectionBuffer,
  isPlaying = true,
  onPlayPause,
  scoreboard,
  sceneMarkers,
  highlightMarkers,
  sentiment,
}: VideoPanelProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [frameSrc, setFrameSrc] = useState(
    "https://images.unsplash.com/photo-1518605368461-1e129623b1bf?auto=format&fit=crop&q=80&w=2000",
  );
  const [detections, setDetections] = useState<Detection[]>([]);
  const [crowdIntensity, setCrowdIntensity] = useState(-1);
  const [muted, setMuted] = useState(true);
  const [videoDuration, setVideoDuration] = useState(0);

  // Interpolation: store previous + current detection frames for lerp
  const prevDetectionsRef = useRef<{ time: number; dets: Detection[] }>({ time: 0, dets: [] });
  const currDetectionsRef = useRef<{ time: number; dets: Detection[] }>({ time: 0, dets: [] });
  const [interpolatedDets, setInterpolatedDets] = useState<Detection[]>([]);

  const isNative = !!videoUrl;
  const effectiveDuration = isNative && videoDuration > 0 ? videoDuration : totalTime;
  const effectiveTime = isNative && videoRef.current ? videoRef.current.currentTime : currentTime;
  const progress = effectiveDuration > 0 ? (effectiveTime / effectiveDuration) * 100 : 0;

  // Interpolate bounding boxes between two detection frames
  const interpolateDetections = useCallback((t: number) => {
    const prev = prevDetectionsRef.current;
    const curr = currDetectionsRef.current;
    if (!curr.dets.length) return;

    const span = curr.time - prev.time;
    if (span <= 0 || t <= prev.time) {
      setInterpolatedDets(curr.dets);
      return;
    }

    const alpha = Math.min(1, (t - prev.time) / span);
    const result: Detection[] = curr.dets.map((det) => {
      // Find matching previous detection by player_id or nearest position
      const prevDet = det.player_id
        ? prev.dets.find((p) => p.player_id === det.player_id)
        : null;
      if (!prevDet) return det;

      const [x1, y1, x2, y2] = det.box;
      const [px1, py1, px2, py2] = prevDet.box;
      return {
        ...det,
        box: [
          px1 + (x1 - px1) * alpha,
          py1 + (y1 - py1) * alpha,
          px2 + (x2 - px2) * alpha,
          py2 + (y2 - py2) * alpha,
        ] as [number, number, number, number],
      };
    });
    setInterpolatedDets(result);
  }, []);

  // Legacy mode: listen for frame updates via CustomEvent
  useEffect(() => {
    if (isNative) return;
    const handleUpdate = (e: any) => {
      const { frame, dets, crowdIntensity: ci } = e.detail;
      if (frame) setFrameSrc(frame);
      if (dets) {
        prevDetectionsRef.current = currDetectionsRef.current;
        currDetectionsRef.current = { time: Date.now() / 1000, dets };
        setDetections(dets);
      }
      if (ci !== undefined) setCrowdIntensity(ci);
    };
    window.addEventListener("vio-frame-update", handleUpdate);
    return () => window.removeEventListener("vio-frame-update", handleUpdate);
  }, [isNative]);

  // Native mode: sync detections from buffer on video timeupdate + interpolate
  const handleTimeUpdate = useCallback(() => {
    if (!videoRef.current || !detectionBuffer) return;
    const t = videoRef.current.currentTime;
    onTimeChange(t);

    const quantized = Math.round(t * 10) / 10;
    const frame =
      detectionBuffer.get(quantized) ?? findNearest(detectionBuffer, quantized, 0.5);
    if (frame) {
      // Check if this is a new detection frame
      if (frame.detections !== currDetectionsRef.current.dets) {
        prevDetectionsRef.current = currDetectionsRef.current;
        currDetectionsRef.current = { time: t, dets: frame.detections };
        setDetections(frame.detections);
      }
      setCrowdIntensity(frame.crowdIntensity);
      interpolateDetections(t);
    }
  }, [detectionBuffer, onTimeChange, interpolateDetections]);

  const handleMetadataLoaded = () => {
    if (videoRef.current) {
      setVideoDuration(videoRef.current.duration);
    }
  };

  // Sync play/pause state to video element
  useEffect(() => {
    if (!videoRef.current || !isNative) return;
    if (isPlaying) {
      videoRef.current.play().catch(() => {});
    } else {
      videoRef.current.pause();
    }
  }, [isPlaying, isNative]);

  // Sync mute state
  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.muted = muted;
    }
  }, [muted]);

  const handleTimelineClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    const targetTime = effectiveDuration * ratio;

    if (isNative && videoRef.current) {
      videoRef.current.currentTime = targetTime;
    }
    onTimeChange(targetTime);
    onSeek?.(targetTime);
  };

  return (
    <div className="flex-1 flex flex-col gap-4 min-w-0">
      <div className="relative flex-1 rounded-xl overflow-hidden glass-panel flex flex-col">
        <div className="flex-1 relative bg-black">
          {/* Video / Frame */}
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
            <img
              src={frameSrc}
              alt="Football Match"
              className="w-full h-full object-contain"
            />
          )}

          {/* YOLO detections overlay (interpolated for smooth tracking) */}
          {(isNative && interpolatedDets.length ? interpolatedDets : detections)
            .filter((det) => det.label !== "Crowd")
            .map((det, i) => {
            const [x1, y1, x2, y2] = det.box;
            const color = labelColor(det);
            const label = detectionLabel(det);
            return (
              <div
                key={det.player_id ?? i}
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
                {label && (
                  <span
                    className="absolute -top-5 left-0 text-[8px] font-bold px-1 py-0.5 rounded-sm whitespace-nowrap"
                    style={{ background: color.bg, color: color.text }}
                  >
                    {label}
                  </span>
                )}
              </div>
            );
          })}

          {/* Scoreboard overlay (from Video Indexer OCR) */}
          {scoreboard && (
            <div className="absolute top-4 right-4 bg-black/70 backdrop-blur border border-white/10 rounded-lg px-4 py-2 flex items-center gap-3 z-10">
              <span className="text-sm font-semibold">{scoreboard.home_team ?? "Home"}</span>
              <span className="text-lg font-bold text-brand-primary">{scoreboard.home_score}</span>
              <span className="text-xs text-brand-muted">-</span>
              <span className="text-lg font-bold text-brand-accent">{scoreboard.away_score}</span>
              <span className="text-sm font-semibold">{scoreboard.away_team ?? "Away"}</span>
            </div>
          )}

          {/* Metrics overlay */}
          <div className="absolute top-4 left-4 flex gap-2 flex-wrap">
            <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
              <span className="text-brand-success text-xs">Players</span>
              <span className="text-xs font-mono">
                {detections.filter((d) => d.label === "Player" || d.label === "GK").length}
              </span>
            </div>
            {detections.some((d) => d.label === "Referee") && (
              <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
                <span className="text-pink-400 text-xs">Refs</span>
                <span className="text-xs font-mono">
                  {detections.filter((d) => d.label === "Referee").length}
                </span>
              </div>
            )}
            <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
              <span className="text-brand-accent text-xs">Ball</span>
              <span className="text-xs font-mono">
                {detections.some((d) => d.label === "Ball") ? "Y" : "-"}
              </span>
            </div>
            {sentiment && SENTIMENT_CONFIG[sentiment] && (
              <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
                <span className={`text-xs ${SENTIMENT_CONFIG[sentiment].color}`}>
                  {SENTIMENT_CONFIG[sentiment].emoji} {SENTIMENT_CONFIG[sentiment].label}
                </span>
              </div>
            )}
            {crowdIntensity >= 0 && (
              <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
                <span
                  className={`text-xs ${
                    crowdIntensity >= 7
                      ? "text-red-400"
                      : crowdIntensity >= 4
                        ? "text-yellow-400"
                        : "text-brand-muted"
                  }`}
                >
                  Crowd
                </span>
                <div className="flex items-center gap-1">
                  <div className="w-12 h-1.5 bg-brand-border rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-300 ${
                        crowdIntensity >= 7
                          ? "bg-red-400"
                          : crowdIntensity >= 4
                            ? "bg-yellow-400"
                            : "bg-brand-muted"
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
            {/* Scene markers from Video Indexer */}
            {sceneMarkers?.map((scene, i) => (
              <div
                key={i}
                className="absolute top-0 w-0.5 h-full bg-brand-accent/40"
                style={{ left: `${(scene.start_sec / effectiveDuration) * 100}%` }}
                title={`Scene at ${formatTime(scene.start_sec)}`}
              />
            ))}
            {/* Highlight markers */}
            {highlightMarkers?.map((hl, i) => {
              const markerColor =
                hl.category === "critical" ? "#ef4444" :
                hl.category === "card" ? "#eab308" :
                hl.category === "foul" ? "#f97316" :
                "#38bdf8";
              return (
                <div
                  key={`hl-${i}`}
                  className="absolute top-0 h-full flex items-center z-[5]"
                  style={{ left: `${(hl.timeSec / effectiveDuration) * 100}%` }}
                  title={`Highlight at ${formatTime(hl.timeSec)}`}
                >
                  <div
                    className="w-2 h-2 rotate-45"
                    style={{ backgroundColor: markerColor, boxShadow: `0 0 4px ${markerColor}` }}
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
              <button className="hover:text-white" onClick={onPlayPause}>
                {isPlaying ? "⏸" : "▶"}
              </button>
              <button className="hover:text-white" onClick={() => setMuted((m) => !m)}>
                {muted ? "🔇" : "🔊"}
              </button>
              <span>
                {formatTime(isNative ? effectiveTime : currentTime)} / {formatTime(effectiveDuration)}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <button className="hover:text-white px-2 py-1 rounded bg-white/5 border border-white/10">
                1x
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
