"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Header, AIMode } from "@/components/Header";
import { VideoPanel } from "@/components/VideoPanel";
import { BottomPanels } from "@/components/BottomPanels";
import { EventsSidebar } from "@/components/EventsSidebar";
import { AlertBanner } from "@/components/AlertBanner";
import type { MatchEvent, Detection, TensionPoint } from "@/types/events";
import type { DetectionFrame } from "@/components/VideoPanel";

const API_VERSION = "0.4.0";

export default function Page() {
  const [status, setStatus] = useState<"idle" | "analyzing" | "paused">("idle");
  const [sourceUrl, setSourceUrl] = useState("");
  const [aiMode, setAiMode] = useState<AIMode>("cloud");
  const [currentTime, setCurrentTime] = useState(0);
  const [events, setEvents] = useState<MatchEvent[]>([]);
  const [totalTime, setTotalTime] = useState(90 * 60);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [teamColors, setTeamColors] = useState<string[]>([]);
  const [tensionHistory, setTensionHistory] = useState<TensionPoint[]>([]);
  const [ballHistory, setBallHistory] = useState<{ x: number; y: number }[]>([]);
  const [alert, setAlert] = useState<MatchEvent | null>(null);
  const [backendVersion, setBackendVersion] = useState<string | null>(null);
  // Hybrid playback state
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [streamFrames, setStreamFrames] = useState(true);
  const detectionBufferRef = useRef<Map<number, DetectionFrame>>(new Map());
  // Scoreboard from Video Indexer
  const [scoreboard, setScoreboard] = useState<{ home_team?: string; away_team?: string; home_score: number; away_score: number } | null>(null);
  const [sceneMarkers, setSceneMarkers] = useState<{ start_sec: number }[]>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const frameRef = useRef(0);
  const fpsRef = useRef(25);
  const sessionIdRef = useRef<string | null>(null);
  // Smooth progress interpolation
  const lastFrameTimeRef = useRef(0);
  const lastFrameArrivalRef = useRef(0);
  const animFrameRef = useRef(0);

  // Build dynamic URLs in the client
  const getBackendHTTP = () => {
    if (typeof window === "undefined") return "/api";
    return process.env.NEXT_PUBLIC_API_URL || `${window.location.protocol}//${window.location.host}/api`;
  };

  const getBackendWS = () => {
    if (typeof window === "undefined") return "";
    return process.env.NEXT_PUBLIC_WS_URL ||
      `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;
  };

  // Send commands to backend via WebSocket
  const sendWsCommand = useCallback((type: string, extra: Record<string, unknown> = {}) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type, ...extra }));
    }
  }, []);

  // Connect WebSocket on mount, reconnect if closed
  const connectWS = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    const wsUrl = getBackendWS();
    if (!wsUrl) return;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (sessionIdRef.current) {
        ws.send(JSON.stringify({ type: "join", session_id: sessionIdRef.current }));
      }
    };

    ws.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data.type === "metadata") {
          fpsRef.current = data.fps || 25;
          if (data.api_version) setBackendVersion(data.api_version);
          if (data.duration > 0) {
            setTotalTime(Math.floor(data.duration));
          }
          // Hybrid playback: capture video URL and mode
          if (data.video_url) setVideoUrl(data.video_url);
          setStreamFrames(data.stream_frames ?? true);
          detectionBufferRef.current.clear();
        } else if (data.type === "indexer_ready") {
          // Video Indexer insights arrived
          if (data.scoreboard) setScoreboard(data.scoreboard);
          if (data.scenes) setSceneMarkers(data.scenes);
        } else if (data.type === "event") {
          frameRef.current = data.frame_index ?? frameRef.current;
          const frameTime = frameRef.current / fpsRef.current;
          lastFrameTimeRef.current = frameTime;
          lastFrameArrivalRef.current = Date.now();

          // Store detections + team colors for BottomPanels
          setDetections(data.detections ?? []);
          if (data.team_colors) setTeamColors(data.team_colors);

          // Buffer detections for native playback sync
          const timeSec = data.time_sec ?? frameTime;
          const quantizedTime = Math.round(timeSec * 10) / 10;
          detectionBufferRef.current.set(quantizedTime, {
            detections: data.detections ?? [],
            teamColors: data.team_colors ?? [],
            crowdIntensity: data.crowd_intensity ?? -1,
          });
          // Evict old entries (keep last 600 = ~60 seconds at 0.1s resolution)
          if (detectionBufferRef.current.size > 600) {
            const keys = Array.from(detectionBufferRef.current.keys()).sort((a, b) => a - b);
            for (let i = 0; i < keys.length - 600; i++) {
              detectionBufferRef.current.delete(keys[i]);
            }
          }

          // Track ball position for heatmap
          const ballDet = (data.detections ?? []).find((d: Detection) => d.label === "Ball");
          if (ballDet) {
            const bx = (ballDet.box[0] + ballDet.box[2]) / 2;
            const by = (ballDet.box[1] + ballDet.box[3]) / 2;
            setBallHistory((prev) => [...prev, { x: bx, y: by }].slice(-500));
          }

          // Build event
          const tensionScore = data.tension_score ?? 0;
          const event: MatchEvent = {
            id: String(data.frame_index ?? Date.now()),
            timestamp: formatTime(frameRef.current, fpsRef.current),
            title: data.event_type ?? "Event",
            description: data.description ?? data.raw_model_output?.slice(0, 120),
            category: mapCategory(data.event_type),
            tensionScore,
          };
          setEvents((prev) => [event, ...prev].slice(0, 100));

          // Track tension for momentum chart
          if (tensionScore > 0 || data.event_type !== "normal_play") {
            setTensionHistory((prev) => {
              const point: TensionPoint = { time: frameTime, score: tensionScore };
              return [...prev, point].slice(-120);
            });
          }

          // Alert for critical events
          if (data.confirmed_goal || data.confirmed_card || data.confirmed_penalty || tensionScore >= 8) {
            setAlert(event);
          }

          // Create engagement event if present
          if (data.engagement?.type && data.engagement?.text) {
            const engCategory =
              data.engagement.type === "poll" ? "poll" as const :
              data.engagement.type === "sentiment" ? "sentiment_prompt" as const :
              "product" as const;
            const engEvent: MatchEvent = {
              id: `eng-${data.frame_index ?? Date.now()}`,
              timestamp: formatTime(frameRef.current, fpsRef.current),
              title: data.engagement.text,
              category: engCategory,
            };
            setEvents((prev) => [engEvent, ...prev].slice(0, 100));
          }

          // Pass frame + detections + audio + team colors to VideoPanel
          if (data.frame_data) {
            window.dispatchEvent(new CustomEvent("vio-frame-update", {
              detail: {
                frame: data.frame_data,
                dets: data.detections ?? [],
                crowdIntensity: data.crowd_intensity ?? -1,
                teamColors: data.team_colors ?? [],
              }
            }));
          }
        } else if (data.type === "status" && data.status === "stopped") {
          sessionIdRef.current = null;
          setStatus("idle");
        } else if (data.type === "error") {
          console.error("Backend error:", data.message);
        }
      } catch (_) {}
    };

    ws.onerror = () => {
      reconnectTimerRef.current = setTimeout(connectWS, 3000);
    };
    ws.onclose = () => {
      reconnectTimerRef.current = setTimeout(connectWS, 3000);
    };
  }, []);

  useEffect(() => {
    connectWS();

    const handleUnload = () => {
      if (sessionIdRef.current && wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "stop" }));
      }
    };
    window.addEventListener("beforeunload", handleUnload);

    return () => {
      window.removeEventListener("beforeunload", handleUnload);
      if (sessionIdRef.current && wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "stop" }));
      }
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [connectWS]);

  // Smooth progress bar (legacy mode only — native mode uses video.currentTime)
  useEffect(() => {
    if (status !== "analyzing" || !streamFrames) {
      cancelAnimationFrame(animFrameRef.current);
      return;
    }
    const tick = () => {
      if (lastFrameArrivalRef.current > 0) {
        const elapsed = (Date.now() - lastFrameArrivalRef.current) / 1000;
        const interpolated = lastFrameTimeRef.current + elapsed;
        setCurrentTime(Math.min(interpolated, totalTime));
      }
      animFrameRef.current = requestAnimationFrame(tick);
    };
    animFrameRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animFrameRef.current);
  }, [status, totalTime, streamFrames]);

  const handleStart = async (url?: string) => {
    const effectiveUrl = url ?? sourceUrl;
    if (!effectiveUrl) return;

    if (sessionIdRef.current) {
      sendWsCommand("stop");
      sessionIdRef.current = null;
    }

    setEvents([]);
    setDetections([]);
    setTeamColors([]);
    setTensionHistory([]);
    setBallHistory([]);
    setAlert(null);
    setVideoUrl(null);
    setStreamFrames(true);
    setScoreboard(null);
    setSceneMarkers([]);
    detectionBufferRef.current.clear();
    frameRef.current = 0;
    fpsRef.current = 25;
    setCurrentTime(0);
    lastFrameTimeRef.current = 0;
    lastFrameArrivalRef.current = 0;
    setTotalTime(90 * 60);
    setStatus("analyzing");

    try {
      const res = await fetch(`${getBackendHTTP()}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: effectiveUrl, ai_mode: aiMode, stream_frames: false }),
      });
      const body = await res.json();
      sessionIdRef.current = body.session_id;
      sendWsCommand("join", { session_id: body.session_id });
    } catch (e) {
      console.error("Failed to start:", e);
      setStatus("idle");
    }
  };

  const handleStop = () => {
    if (sessionIdRef.current) {
      sendWsCommand("stop");
      sessionIdRef.current = null;
    }
    setStatus("idle");
  };

  const handleSeek = (time: number) => {
    lastFrameTimeRef.current = time;
    lastFrameArrivalRef.current = Date.now();
    setCurrentTime(time);
    sendWsCommand("seek", { time });
  };

  const handleStatusChange = (s: "idle" | "analyzing" | "paused", url?: string) => {
    if (s === "analyzing" && status === "idle") {
      handleStart(url);
    } else if (s === "idle") {
      handleStop();
    } else if (s === "paused" && status === "analyzing") {
      sendWsCommand("pause");
      setStatus("paused");
    } else if (s === "analyzing" && status === "paused") {
      sendWsCommand("resume");
      setStatus("analyzing");
    } else {
      setStatus(s);
    }
  };

  return (
    <div className="h-screen flex flex-col">
      <Header
        status={status}
        onStatusChange={handleStatusChange}
        sourceUrl={sourceUrl}
        onSourceUrlChange={setSourceUrl}
        aiMode={aiMode}
        onAIModeChange={setAiMode}
        frontendVersion={API_VERSION}
        backendVersion={backendVersion}
      />

      <AlertBanner event={alert} onDismiss={() => setAlert(null)} />

      <main className="flex-1 flex overflow-hidden p-4 gap-4">
        <div className="flex-1 flex flex-col gap-4 min-w-0">
          <VideoPanel
            currentTime={currentTime}
            totalTime={totalTime}
            onTimeChange={setCurrentTime}
            onSeek={handleSeek}
            videoUrl={streamFrames ? null : videoUrl}
            detectionBuffer={detectionBufferRef.current}
            isPlaying={status === "analyzing"}
            onPlayPause={() => {
              if (status === "analyzing") handleStatusChange("paused");
              else if (status === "paused") handleStatusChange("analyzing");
            }}
            scoreboard={scoreboard}
            sceneMarkers={sceneMarkers}
          />
          <BottomPanels detections={detections} tensionHistory={tensionHistory} teamColors={teamColors} ballHistory={ballHistory} />
        </div>

        <EventsSidebar events={events} />
      </main>
    </div>
  );
}

// Helpers
function formatTime(frameIndex: number, fps: number = 25): string {
  const seconds = Math.floor(frameIndex / fps);
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function mapCategory(eventType?: string): MatchEvent["category"] {
  if (!eventType) return "match";
  const t = eventType.toLowerCase();
  if (t === "goal" || t === "goal_chance" || t === "penalty") return "critical";
  if (t === "celebration") return "key_action";
  if (t === "yellow_card" || t === "red_card") return "card";
  if (t === "foul") return "foul";
  if (t === "corner" || t === "free_kick" || t === "offside" || t === "substitution") return "set_piece";
  if (t.includes("error") || t.includes("system")) return "system";
  return "match";
}
