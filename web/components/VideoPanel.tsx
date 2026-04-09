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
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60).toString().padStart(2, "0");
  const s = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function labelColor(label: string): { border: string; bg: string; text: string } {
  const l = label.toLowerCase();
  if (l === "ball") return { border: "#ffffff", bg: "#ffffff", text: "#000" };
  if (l === "gk") return { border: "#facc15", bg: "#facc15", text: "#000" };
  return { border: "#FE9330", bg: "#FE9330", text: "#000" };
}

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
}: VideoPanelProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [frameSrc, setFrameSrc] = useState(
    "https://images.unsplash.com/photo-1518605368461-1e129623b1bf?auto=format&fit=crop&q=80&w=2000",
  );
  const [detections, setDetections] = useState<Detection[]>([]);
  const [crowdIntensity, setCrowdIntensity] = useState(-1);
  const [muted, setMuted] = useState(true);
  const [videoDuration, setVideoDuration] = useState(0);

  const isNative = !!videoUrl;
  const effectiveDuration = isNative && videoDuration > 0 ? videoDuration : totalTime;
  const effectiveTime = isNative && videoRef.current ? videoRef.current.currentTime : currentTime;
  const progress = effectiveDuration > 0 ? (effectiveTime / effectiveDuration) * 100 : 0;

  // Legacy mode: listen for frame updates via CustomEvent
  useEffect(() => {
    if (isNative) return;
    const handleUpdate = (e: any) => {
      const { frame, dets, crowdIntensity: ci } = e.detail;
      if (frame) setFrameSrc(frame);
      if (dets) setDetections(dets);
      if (ci !== undefined) setCrowdIntensity(ci);
    };
    window.addEventListener("vio-frame-update", handleUpdate);
    return () => window.removeEventListener("vio-frame-update", handleUpdate);
  }, [isNative]);

  // Native mode: sync detections from buffer on video timeupdate
  const handleTimeUpdate = useCallback(() => {
    if (!videoRef.current || !detectionBuffer) return;
    const t = videoRef.current.currentTime;
    onTimeChange(t);

    const quantized = Math.round(t * 10) / 10;
    const frame =
      detectionBuffer.get(quantized) ?? findNearest(detectionBuffer, quantized, 0.5);
    if (frame) {
      setDetections(frame.detections);
      setCrowdIntensity(frame.crowdIntensity);
    }
  }, [detectionBuffer, onTimeChange]);

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

          {/* YOLO detections overlay */}
          {detections.map((det, i) => {
            const [x1, y1, x2, y2] = det.box;
            const color = labelColor(det.label);
            return (
              <div
                key={i}
                className="absolute pointer-events-none"
                style={{
                  left: `${x1 * 100}%`,
                  top: `${y1 * 100}%`,
                  width: `${(x2 - x1) * 100}%`,
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
                {detections.some((d) => d.label === "Ball") ? "Y" : "-"}
              </span>
            </div>
            {isNative && (
              <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg px-3 py-1.5 flex items-center gap-2">
                <span className="text-green-400 text-xs">Native</span>
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
