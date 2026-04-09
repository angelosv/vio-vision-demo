"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Header, AIMode } from "@/components/Header";
import { VideoPanel } from "@/components/VideoPanel";
import { BottomPanels } from "@/components/BottomPanels";
import { EventsSidebar } from "@/components/EventsSidebar";
import { AlertBanner } from "@/components/AlertBanner";
import type { MatchEvent, Detection, TensionPoint } from "@/types/events";

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
  const [alert, setAlert] = useState<MatchEvent | null>(null);
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
          if (data.duration > 0) {
            setTotalTime(Math.floor(data.duration));
          }
        } else if (data.type === "event") {
          frameRef.current = data.frame_index ?? frameRef.current;
          const frameTime = frameRef.current / fpsRef.current;
          lastFrameTimeRef.current = frameTime;
          lastFrameArrivalRef.current = Date.now();

          // Store detections + team colors for BottomPanels
          setDetections(data.detections ?? []);
          if (data.team_colors) setTeamColors(data.team_colors);

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

          // Alert for critical events (tension >= 8) or confirmed goals
          if (data.confirmed_goal || tensionScore >= 8) {
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

  // Smooth progress bar
  useEffect(() => {
    if (status !== "analyzing") {
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
  }, [status, totalTime]);

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
    setAlert(null);
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
        body: JSON.stringify({ url: effectiveUrl, ai_mode: aiMode }),
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
      />

      <AlertBanner event={alert} onDismiss={() => setAlert(null)} />

      <main className="flex-1 flex overflow-hidden p-4 gap-4">
        <div className="flex-1 flex flex-col gap-4 min-w-0">
          <VideoPanel
            currentTime={currentTime}
            totalTime={totalTime}
            onTimeChange={setCurrentTime}
            onSeek={handleSeek}
          />
          <BottomPanels detections={detections} tensionHistory={tensionHistory} teamColors={teamColors} />
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
  if (t.includes("goal") || t.includes("critical")) return "critical";
  if (t.includes("celebration") || t.includes("key")) return "key_action";
  if (t.includes("error") || t.includes("system")) return "system";
  return "match";
}
