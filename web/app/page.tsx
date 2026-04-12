"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Header, AIMode } from "@/components/Header";
import { VideoPanel } from "@/components/VideoPanel";
import { BottomPanels } from "@/components/BottomPanels";
import { EventsSidebar } from "@/components/EventsSidebar";
import { LiveDataFeed } from "@/components/LiveDataFeed";
import type {
  MatchEvent, TrackedObject, Ball, Possession, TensionPoint,
  HighlightMoment, EventType, EventCategory,
} from "@/types/events";
import { HighlightReel } from "@/components/HighlightReel";

const API_VERSION = "0.5.0";

export default function Page() {
  const [status, setStatus] = useState<"idle" | "analyzing" | "paused">("idle");
  const [sourceUrl, setSourceUrl] = useState("");
  const [aiMode, setAiMode] = useState<AIMode>("cloud");
  const [currentTime, setCurrentTime] = useState(0);
  const [events, setEvents] = useState<MatchEvent[]>([]);
  const [totalTime, setTotalTime] = useState(90 * 60);
  const [tracks, setTracks] = useState<TrackedObject[]>([]);
  const [ball, setBall] = useState<Ball | null>(null);
  const [teamColors, setTeamColors] = useState<string[]>([]);
  const [possession, setPossession] = useState<Possession>({
    team: null, home_percent: 50, away_percent: 50,
  });
  const [tensionHistory, setTensionHistory] = useState<TensionPoint[]>([]);
  const [ballHistory, setBallHistory] = useState<{ x: number; y: number }[]>([]);
  const [backendVersion, setBackendVersion] = useState<string | null>(null);
  // Hybrid playback state
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  // Scoreboard from Video Indexer
  const [scoreboard, setScoreboard] = useState<{ home_team?: string; away_team?: string; home_score: number; away_score: number } | null>(null);
  const [sceneMarkers, setSceneMarkers] = useState<{ start_sec: number }[]>([]);
  // Highlight Reel
  const [highlights, setHighlights] = useState<HighlightMoment[]>([]);
  const [showHighlightReel, setShowHighlightReel] = useState(false);
  // Sentiment + crowd for VideoPanel metrics
  const [sentiment, setSentiment] = useState<string | null>(null);
  const [crowdIntensity, setCrowdIntensity] = useState(-1);
  // Live data feed (raw WS messages for demo)
  const [wsMessages, setWsMessages] = useState<any[]>([]);
  // Dedup for sentiment-change events (only emit on transition)
  const prevSentimentRef = useRef<string | null>(null);
  // Dedup possession milestones (once per minute per team)
  const triggeredMilestonesRef = useRef<Set<string>>(new Set());

  const pushEvent = useCallback((event: MatchEvent) => {
    setEvents((prev) => [event, ...prev].slice(0, 200));
  }, []);

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
        // Live data feed: keep last 200 messages for demo panel
        setWsMessages((prev) => [...prev, data].slice(-200));

        if (data.type === "metadata") {
          fpsRef.current = data.fps || 25;
          if (data.api_version) setBackendVersion(data.api_version);
          if (data.duration > 0) setTotalTime(Math.floor(data.duration));
          if (data.video_url) setVideoUrl(data.video_url);
          return;
        }

        if (data.type === "indexer_ready") {
          if (data.scoreboard) setScoreboard(data.scoreboard);
          if (data.scenes) setSceneMarkers(data.scenes);
          return;
        }

        if (data.type === "status" && (data.status === "stopped" || data.status === "finished")) {
          sessionIdRef.current = null;
          setStatus("idle");
          if (data.status === "finished") setShowHighlightReel(true);
          return;
        }

        if (data.type === "error") {
          console.error("Backend error:", data.message);
          setEvents((prev) => [{
            id: `err-${Date.now()}`,
            timestamp: formatTime(frameRef.current, fpsRef.current),
            title: "System error",
            description: data.message,
            category: "system",
            eventType: "error",
          }, ...prev].slice(0, 200));
          return;
        }

        if (data.type !== "event") return;

        // ─── Native microservices event payload ───────────────────
        frameRef.current = data.frame_index ?? frameRef.current;
        const timeSec = data.frame_time_sec ?? frameRef.current / fpsRef.current;
        lastFrameTimeRef.current = timeSec;
        lastFrameArrivalRef.current = Date.now();

        // Tracks, ball, team colors, possession, crowd, sentiment
        const newTracks: TrackedObject[] = data.tracks ?? [];
        setTracks(newTracks);
        setBall(data.ball ?? null);
        if (data.team_colors) setTeamColors(data.team_colors);
        if (data.possession) setPossession(data.possession);
        if (typeof data.crowd_intensity === "number") {
          setCrowdIntensity(data.crowd_intensity);
        }

        // Ball heatmap history
        if (data.ball?.position) {
          const { x, y } = data.ball.position;
          setBallHistory((prev) => [...prev, { x, y }].slice(-500));
        }

        // Sentiment transition → emit as event
        if (data.sentiment && data.sentiment !== prevSentimentRef.current) {
          const prev = prevSentimentRef.current;
          prevSentimentRef.current = data.sentiment;
          setSentiment(data.sentiment);
          if (prev !== null) {
            pushEvent({
              id: `sent-${frameRef.current}`,
              timestamp: formatTime(frameRef.current, fpsRef.current),
              title: `Sentiment: ${prev} → ${data.sentiment}`,
              category: "sentiment",
              eventType: "sentiment_change",
            });
          }
        }

        // Match event
        const evt = data.event;
        const tension = evt?.tension_score ?? 0;
        if (evt?.event_type && evt.event_type !== "normal_play") {
          const category = mapCategory(evt.event_type);
          pushEvent({
            id: `evt-${frameRef.current}-${evt.event_type}`,
            timestamp: formatTime(frameRef.current, fpsRef.current),
            title: eventLabel(evt.event_type),
            description: evt.description,
            category,
            eventType: evt.event_type as EventType,
            tensionScore: tension,
            confirmed: evt.confirmed,
            team: evt.team,
          });

          // Track tension
          setTensionHistory((prev) => [
            ...prev, { time: timeSec, score: tension },
          ].slice(-180));

          // Highlight collection
          const isHighlight =
            tension >= 7 || evt.confirmed ||
            ["goal", "penalty", "red_card", "yellow_card"].includes(evt.event_type);
          if (isHighlight && evt.description) {
            setHighlights((prev) => {
              const dup = prev.some((h) => Math.abs(h.timeSec - timeSec) < 5);
              if (dup) return prev;
              return [...prev, {
                id: `hl-${frameRef.current}`,
                timeSec,
                timestamp: formatTime(frameRef.current, fpsRef.current),
                eventType: evt.event_type,
                description: evt.description,
                tensionScore: tension,
                category,
                confirmed: !!evt.confirmed,
              }].sort((a, b) => a.timeSec - b.timeSec);
            });
          }
        } else if (tension > 0) {
          // Track tension even for normal_play
          setTensionHistory((prev) => [
            ...prev, { time: timeSec, score: tension },
          ].slice(-180));
        }

        // Possession milestone (>= 70% for either team)
        if (data.possession?.team) {
          const pct = data.possession.team === "home"
            ? data.possession.home_percent
            : data.possession.away_percent;
          if (pct >= 70) {
            const milestoneId = `poss-${data.possession.team}-${Math.floor(timeSec / 60)}`;
            if (!triggeredMilestonesRef.current.has(milestoneId)) {
              triggeredMilestonesRef.current.add(milestoneId);
              pushEvent({
                id: milestoneId,
                timestamp: formatTime(frameRef.current, fpsRef.current),
                title: `${data.possession.team} possession at ${pct.toFixed(0)}%`,
                category: "possession",
                eventType: "possession_milestone",
                team: data.possession.team,
              });
            }
          }
        }
      } catch (e) {
        console.error("WS parse error", e);
      }
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

  // Smooth progress bar — native video playback handles time itself, this is
  // a fallback for when there's no video_url (legacy MP4 analysis mode).
  useEffect(() => {
    if (status !== "analyzing" || videoUrl) {
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
  }, [status, totalTime, videoUrl]);

  const handleStart = async (url?: string) => {
    const effectiveUrl = url ?? sourceUrl;
    if (!effectiveUrl) return;

    // Stop any existing session before starting a new one
    try {
      await fetch(`${getBackendHTTP()}/stop`, { method: "POST" }).catch(() => {});
    } catch {}

    setEvents([]);
    setTracks([]);
    setBall(null);
    setTeamColors([]);
    setPossession({ team: null, home_percent: 50, away_percent: 50 });
    setTensionHistory([]);
    setBallHistory([]);
    setVideoUrl(null);
    setScoreboard(null);
    setSceneMarkers([]);
    setHighlights([]);
    setShowHighlightReel(false);
    setSentiment(null);
    setCrowdIntensity(-1);
    setWsMessages([]);
    prevSentimentRef.current = null;
    triggeredMilestonesRef.current.clear();
    frameRef.current = 0;
    setCurrentTime(0);
    setStatus("analyzing");

    try {
      const res = await fetch(`${getBackendHTTP()}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: effectiveUrl }),
      });
      const body = await res.json();
      if (body.session_id) {
        sessionIdRef.current = body.session_id;
        sendWsCommand("join", { session_id: body.session_id });
      }
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
    if (highlights.length > 0) {
      setShowHighlightReel(true);
    }
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

      <main className="flex-1 flex overflow-hidden p-4 gap-4 main-layout">
        <div className="flex-1 flex flex-col gap-3 min-w-0">
          <VideoPanel
            currentTime={currentTime}
            totalTime={totalTime}
            onTimeChange={setCurrentTime}
            onSeek={handleSeek}
            videoUrl={videoUrl}
            tracks={tracks}
            ball={ball}
            crowdIntensity={crowdIntensity}
            sentiment={sentiment}
            isPlaying={status === "analyzing"}
            onPlayPause={() => {
              if (status === "analyzing") handleStatusChange("paused");
              else if (status === "paused") handleStatusChange("analyzing");
            }}
            scoreboard={scoreboard}
            sceneMarkers={sceneMarkers}
            highlightMarkers={highlights.map((h) => ({ timeSec: h.timeSec, category: h.category }))}
          />
          <BottomPanels
            tracks={tracks}
            ball={ball}
            teamColors={teamColors}
            ballHistory={ballHistory}
            possession={possession}
            tensionHistory={tensionHistory}
          />
        </div>

        <div className="sidebar-panel flex gap-3">
          <EventsSidebar events={events} />
          <LiveDataFeed messages={wsMessages} />
        </div>
      </main>

      {/* Highlight Reel modal */}
      {showHighlightReel && highlights.length > 0 && (
        <HighlightReel
          highlights={highlights}
          onSeek={(timeSec) => {
            setShowHighlightReel(false);
            handleSeek(timeSec);
          }}
          onClose={() => setShowHighlightReel(false)}
        />
      )}

      {/* Floating button to re-open highlights */}
      {status === "idle" && highlights.length > 0 && !showHighlightReel && (
        <button
          onClick={() => setShowHighlightReel(true)}
          className="fixed bottom-6 right-6 z-40 px-4 py-2 bg-brand-accent text-black font-semibold rounded-full shadow-lg hover:bg-brand-accent/90 transition-colors text-sm"
        >
          Highlights ({highlights.length})
        </button>
      )}
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

function mapCategory(eventType?: string): EventCategory {
  if (!eventType) return "match";
  const t = eventType.toLowerCase();
  if (t === "goal" || t === "goal_chance" || t === "penalty") return "critical";
  if (t === "celebration" || t === "shot_on_goal" || t === "fast_break") return "key_action";
  if (t === "yellow_card" || t === "red_card") return "card";
  if (t === "foul") return "foul";
  if (t === "corner" || t === "free_kick" || t === "offside" || t === "substitution") return "set_piece";
  if (t === "possession_change" || t === "possession_milestone") return "possession";
  if (t === "stoppage" || t === "out_of_bounds") return "stoppage";
  if (t === "sentiment_change" || t === "crowd_reaction") return "sentiment";
  if (t === "poll") return "poll";
  if (t === "sentiment_prompt") return "sentiment_prompt";
  if (t === "product") return "product";
  if (t.includes("error") || t.includes("system")) return "system";
  return "match";
}

function eventLabel(eventType: string): string {
  const label = eventType.replace(/_/g, " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}
