"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Header, AIMode } from "@/components/Header";
import { VideoPanel } from "@/components/VideoPanel";
import { BottomPanels } from "@/components/BottomPanels";
import { EventsSidebar } from "@/components/EventsSidebar";
import type { MatchEvent } from "@/types/events";

const isLocal = typeof window !== "undefined" && window.location.hostname === "localhost";
const BACKEND_HTTP = isLocal
  ? "http://localhost:8080"
  : `${window.location.protocol}//${window.location.host}/api`;
const BACKEND_WS = isLocal
  ? "ws://localhost:8080/ws"
  : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws`;

export default function Page() {
  const [status, setStatus] = useState<"idle" | "analyzing" | "paused">("idle");
  const [sourceUrl, setSourceUrl] = useState("");
  const [aiMode, setAiMode] = useState<AIMode>("cloud");
  const [currentTime, setCurrentTime] = useState(0);
  const [events, setEvents] = useState<MatchEvent[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const frameRef = useRef(0);
  const totalTime = 90 * 60;

  // Connect WebSocket on mount, reconnect if closed
  const connectWS = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    const ws = new WebSocket(BACKEND_WS);
    wsRef.current = ws;

    ws.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data.type === "event") {
          frameRef.current = data.frame_index ?? frameRef.current;
          // Advance time roughly: ~25fps → each frame ≈ 0.04s
          setCurrentTime(Math.floor(frameRef.current * 0.04));

          const event: MatchEvent = {
            id: String(data.frame_index ?? Date.now()),
            timestamp: formatTime(frameRef.current),
            title: data.event_type ?? "Event",
            description: data.description ?? data.raw_model_output?.slice(0, 120),
            category: mapCategory(data.event_type),
          };
          setEvents((prev) => [event, ...prev].slice(0, 50));

          // Pass frame + detections to VideoPanel
          if (data.frame_data) {
            window.dispatchEvent(new CustomEvent("vio-frame-update", {
              detail: { frame: data.frame_data, dets: data.detections ?? [] }
            }));
          }
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
    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [connectWS]);

  const handleStart = async (url?: string) => {
    const effectiveUrl = url ?? sourceUrl;
    if (!effectiveUrl) return;
    setEvents([]);
    frameRef.current = 0;
    setCurrentTime(0);
    setStatus("analyzing");
    try {
      await fetch(`${BACKEND_HTTP}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: effectiveUrl, ai_mode: aiMode }),
      });
    } catch (e) {
      console.error("Failed to start:", e);
      setStatus("idle");
    }
  };

  // Wire status changes: pause just stops UI updates (backend keeps running)
  const handleStatusChange = (s: "idle" | "analyzing" | "paused", url?: string) => {
    if (s === "analyzing" && status === "idle") {
      handleStart(url);
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

      <main className="flex-1 flex overflow-hidden p-4 gap-4">
        <div className="flex-1 flex flex-col gap-4 min-w-0">
          <VideoPanel
            currentTime={currentTime}
            totalTime={totalTime}
            onTimeChange={setCurrentTime}
          />
          <BottomPanels />
        </div>

        <EventsSidebar events={events} />
      </main>
    </div>
  );
}

// Helpers
function formatTime(frameIndex: number): string {
  const seconds = Math.floor(frameIndex * 0.04);
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
