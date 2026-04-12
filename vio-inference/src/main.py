"""vio-inference: consume frames from Redis, run ML, publish detections.

Subscribes to `session:{id}:frames` pub/sub. On each announced frame:
  1. Read JPEG bytes from Redis
  2. Decode to numpy
  3. Run YOLO + ByteTrack
  4. Classify teams (3-cluster)
  5. Jersey OCR (async, batched)
  6. Publish detections to `session:{id}:detections`
  7. Persist significant events to Postgres

Runs on a GPU pod. Auto-scales on lag via KEDA (future).
"""

import asyncio
import json
import os
import time
from typing import Dict, Optional, Set

import cv2
import numpy as np
import redis.asyncio as aioredis
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Gauge, Histogram, generate_latest

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from yolo_tracker import YoloTracker, classify_teams
from jersey_ocr import JerseyOcr
from event_detector import EventDetector
from ai_enrichment import AIEnrichment
from audio_analyzer import AudioAnalyzer


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
AI_INTERVAL_SEC = float(os.getenv("AI_INTERVAL_SEC", "3.0"))  # GPT-4o call interval

# Prometheus metrics
m_sessions_active = Gauge("vio_inference_sessions_active", "Active inference sessions")
m_frames_processed = Counter(
    "vio_inference_frames_processed_total",
    "Frames processed by YOLO+ByteTrack",
)
m_events_emitted = Counter(
    "vio_inference_events_emitted_total",
    "Heuristic events emitted",
    labelnames=["event_type"],
)
m_ai_enrichments = Counter(
    "vio_inference_ai_enrichments_total",
    "GPT-4o enrichment calls",
    labelnames=["trigger"],
)
m_frame_latency = Histogram(
    "vio_inference_frame_latency_seconds",
    "End-to-end frame inference latency",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)


app = FastAPI(title="Vio Vision Inference Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class InferenceSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.tracker = YoloTracker()
        self.ocr = JerseyOcr()
        self.detector = EventDetector(session_id)
        self.enrichment = AIEnrichment()
        self.audio: Optional[AudioAnalyzer] = None
        self.audio_loading = False
        self.last_event_time = 0.0
        self.frames_processed = 0


# ── Global state
sessions: Dict[str, InferenceSession] = {}
redis_client: Optional[aioredis.Redis] = None


@app.on_event("startup")
async def startup():
    global redis_client
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=False)
    asyncio.create_task(_listen_for_new_sessions())
    print("[inference] started, listening for sessions")


@app.on_event("shutdown")
async def shutdown():
    if redis_client:
        await redis_client.close()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "vio-inference",
        "active_sessions": len(sessions),
        "sessions": [
            {"id": s.session_id, "frames": s.frames_processed}
            for s in sessions.values()
        ],
    }


@app.get("/metrics")
async def prometheus_metrics():
    m_sessions_active.set(len(sessions))
    return Response(content=generate_latest(), media_type="text/plain; version=0.0.4")


async def _listen_for_new_sessions() -> None:
    """Listen for new session announcements on a discovery channel.

    Ingestion publishes `session:{id}:frames` when it starts — we subscribe
    dynamically. For now we use Redis KEYSPACE notifications as discovery.
    Simpler approach for the demo: listen on a fixed discovery channel.
    """
    assert redis_client is not None
    pubsub = redis_client.pubsub()
    # Pattern subscribe to all session frame channels
    await pubsub.psubscribe("session:*:frames")

    async for msg in pubsub.listen():
        if msg["type"] != "pmessage":
            continue
        try:
            data = json.loads(msg["data"])
            sid = data.get("session_id")
            if not sid:
                continue

            msg_type = data.get("type")
            if msg_type == "metadata":
                # New session starting
                if sid not in sessions:
                    sess = InferenceSession(sid)
                    sessions[sid] = sess
                    asyncio.create_task(_process_session(sid))
                    # Kick off audio extraction in background (ffmpeg is blocking)
                    url = data.get("url")
                    if url and not sess.audio_loading:
                        sess.audio_loading = True
                        asyncio.create_task(_load_audio_background(sess, url))
                    print(f"[{sid}] inference session created (source: {data.get('source_type')})")
            elif msg_type == "end":
                sessions.pop(sid, None)
                print(f"[{sid}] inference session ended")
        except Exception as e:
            print(f"[inference] discovery error: {e}")


async def _load_audio_background(session: InferenceSession, url: str) -> None:
    """Extract audio for crowd intensity in a worker thread."""
    def _extract():
        try:
            return AudioAnalyzer(url)
        except Exception as e:
            print(f"[{session.session_id}] audio extraction failed: {e}")
            return None
    analyzer = await asyncio.to_thread(_extract)
    if analyzer and analyzer.available:
        session.audio = analyzer
        print(f"[{session.session_id}] audio ready ({analyzer.duration:.1f}s)")
    else:
        print(f"[{session.session_id}] audio unavailable")


async def _process_session(session_id: str) -> None:
    """Subscribe to this session's frame channel and process each frame."""
    assert redis_client is not None
    session = sessions.get(session_id)
    if not session:
        return

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"session:{session_id}:frames")

    try:
        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            try:
                data = json.loads(msg["data"])
                if data.get("type") in ("metadata", "end"):
                    if data.get("type") == "end":
                        break
                    continue

                redis_key = data["redis_key"]
                frame_idx = data["frame_idx"]
                timestamp_ms = data["timestamp_ms"]
                fps = data.get("fps", 25.0)

                # Read frame bytes
                frame_bytes = await redis_client.get(redis_key)
                if frame_bytes is None:
                    continue  # TTL expired

                # Decode JPEG
                arr = np.frombuffer(frame_bytes, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is None:
                    continue

                # Run inference
                await _infer_frame(session, frame, frame_idx, timestamp_ms, fps)

            except Exception as e:
                print(f"[{session_id}] frame error: {e}")
    finally:
        await pubsub.unsubscribe()
        sessions.pop(session_id, None)


async def _infer_frame(
    session: InferenceSession,
    frame: np.ndarray,
    frame_idx: int,
    timestamp_ms: int,
    fps: float,
) -> None:
    """Single frame inference: YOLO → teams → OCR → publish."""
    now = time.time()

    # YOLO + ByteTrack (blocking, runs on GPU)
    detections = session.tracker.detect_and_track(frame)

    # Team classification via jersey color clustering
    detections, team_colors = classify_teams(frame, detections)

    # Jersey OCR (async via Azure AI Vision)
    session.ocr.process(frame, detections, now)

    # Annotate with jersey numbers we've seen
    for det in detections:
        tid = det.get("track_id")
        if tid is not None:
            det["jersey_number"] = session.ocr.get_jersey(tid)

    # Compose output payload
    time_sec = frame_idx / fps
    tracks_out = [_detection_to_track(d) for d in detections if d.get("label") != "Ball"]
    ball_out = _detection_to_ball(detections)

    # Run event detector (heuristics)
    events = session.detector.process(tracks_out, ball_out, frame_idx, time_sec)
    heuristic_event = events[0] if events else None
    possession = session.detector.possession_state()
    tension = session.detector.tension_score

    # Audio crowd intensity (if extracted)
    crowd = session.audio.get_crowd_intensity(time_sec) if session.audio else -1.0

    # AI enrichment — only fires on significant events / high tension
    enriched = None
    if session.enrichment.enabled:
        enriched = await session.enrichment.maybe_enrich(
            frame, heuristic_event, tension, crowd, time_sec, possession,
        )

    # Merge heuristic + AI event
    event_out = heuristic_event
    if enriched:
        # AI can override/refine the event type + description
        event_out = {
            **(heuristic_event or {}),
            "event_type": enriched.get("event_type") or (heuristic_event or {}).get("event_type", "normal_play"),
            "description": enriched.get("description") or (heuristic_event or {}).get("description"),
            "confirmed": enriched.get("confirmed", False),
            "tension_score": tension,
        }

    payload = {
        "session_id": session.session_id,
        "frame_index": frame_idx,
        "timestamp_ms": timestamp_ms,
        "frame_time_sec": time_sec,
        "tracks": tracks_out,
        "ball": ball_out,
        "team_colors": team_colors,
        "possession": possession,
        "event": event_out,
        "crowd_intensity": crowd,
        "sentiment": enriched.get("sentiment") if enriched else None,
    }

    # Publish to gateway via Redis pub/sub
    await redis_client.publish(
        f"session:{session.session_id}:detections",
        json.dumps(payload),
    )

    session.frames_processed += 1
    m_frames_processed.inc()
    if event_out:
        m_events_emitted.labels(event_type=event_out.get("event_type", "unknown")).inc()
    if enriched:
        trigger = heuristic_event.get("event_type") if heuristic_event else "tension"
        m_ai_enrichments.labels(trigger=trigger).inc()


def _detection_to_track(det: Dict) -> Dict:
    """Convert internal detection to protocol track."""
    x1, y1, x2, y2 = det["box"]
    return {
        "track_id": det.get("track_id"),
        "type": det.get("label", "Player"),
        "team": det.get("team", "home"),
        "jersey_number": det.get("jersey_number", ""),
        "position": {"x": (x1 + x2) / 2, "y": (y1 + y2) / 2},
        "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        "confidence": det.get("confidence", 0),
        "team_color": det.get("color", ""),
    }


def _detection_to_ball(detections) -> Optional[Dict]:
    ball = next((d for d in detections if d.get("label") == "Ball"), None)
    if not ball:
        return None
    x1, y1, x2, y2 = ball["box"]
    return {
        "position": {"x": (x1 + x2) / 2, "y": (y1 + y2) / 2},
        "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        "confidence": ball.get("confidence", 0),
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8002"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
