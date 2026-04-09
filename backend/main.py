import asyncio
import json
import os
import uuid
from typing import Dict, List, Optional, Set

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass  # dotenv optional — env vars can be set externally

import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from stream_reader import VideoStream
from audio_analyzer import AudioAnalyzer
from analyzer import AIService, detect_objects, extract_team_colors, frame_to_jpeg_base64
from database import Database
from video_indexer import VideoIndexerClient, VideoIndexerInsights


API_VERSION = "0.4.1"

app = FastAPI(title="Vio Vision Demo Backend", version=API_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db = Database()


@app.on_event("startup")
async def startup():
    await db.init()


@app.on_event("shutdown")
async def shutdown():
    await db.close()


class StartRequest(BaseModel):
    url: str
    ai_mode: str = "cloud"  # "cloud" (GPT-4o Azure) or "local" (Gemma)
    stream_frames: bool = False  # when False, skip base64 frame encoding (hybrid playback)


# ─── Session management ─────────────────────────────────────────────────────

MAX_CONCURRENT_SESSIONS = 2  # Demo limit: max 2 concurrent analyses

class AnalysisSession:
    """Encapsulates all state for one analysis run, scoped to its clients."""

    def __init__(self, session_id: str, url: str, ai_mode: str, stream_frames: bool = False):
        self.id = session_id
        self.url = url
        self.stream_frames = stream_frames
        self.ai_service = AIService(mode=ai_mode)
        self.stream: Optional[VideoStream] = None
        self.audio: Optional[AudioAnalyzer] = None
        self.running = asyncio.Event()
        self.running.set()
        self.cancelled = False
        self.seek_target: Optional[int] = None
        self.task: Optional[asyncio.Task] = None
        self.clients: Set[WebSocket] = set()
        # Video Indexer
        self.indexer_insights: Optional[VideoIndexerInsights] = None
        self.indexer_task: Optional[asyncio.Task] = None

    async def broadcast(self, message: dict) -> None:
        dead: List[WebSocket] = []
        for ws in self.clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def cancel(self) -> None:
        self.cancelled = True
        self.running.set()  # unblock if paused so the loop can exit
        if self.task and not self.task.done():
            self.task.cancel()
        if self.indexer_task and not self.indexer_task.done():
            self.indexer_task.cancel()
        if self.stream:
            self.stream.release()
            self.stream = None


sessions: Dict[str, AnalysisSession] = {}


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []
        # Track which session each WebSocket belongs to
        self.ws_session: Dict[int, str] = {}  # id(ws) -> session_id

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        # Remove from its session; cancel session if no clients left
        session_id = self.ws_session.pop(id(websocket), None)
        if session_id and session_id in sessions:
            session = sessions[session_id]
            session.clients.discard(websocket)
            if not session.clients:
                session.cancel()
                sessions.pop(session_id, None)

    def join_session(self, websocket: WebSocket, session_id: str) -> None:
        # Leave previous session if any
        old_id = self.ws_session.get(id(websocket))
        if old_id and old_id != session_id and old_id in sessions:
            old_session = sessions[old_id]
            old_session.clients.discard(websocket)
            if not old_session.clients:
                old_session.cancel()
                sessions.pop(old_id, None)
        # Join new session
        self.ws_session[id(websocket)] = session_id
        if session_id in sessions:
            sessions[session_id].clients.add(websocket)

    def get_session(self, websocket: WebSocket) -> Optional[AnalysisSession]:
        session_id = self.ws_session.get(id(websocket))
        if session_id:
            return sessions.get(session_id)
        return None


manager = ConnectionManager()


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/api")
async def root() -> dict:
    return {"status": "ok", "message": "Vio Vision Demo Backend", "api_version": API_VERSION}


@app.post("/api/start")
async def start_demo(req: StartRequest) -> dict:
    """Start a new analysis session and return its ID."""
    # Enforce concurrent session limit
    if len(sessions) >= MAX_CONCURRENT_SESSIONS:
        raise HTTPException(
            status_code=429,
            detail=f"Maximum concurrent sessions ({MAX_CONCURRENT_SESSIONS}) reached. Please wait for other analyses to complete."
        )

    session_id = uuid.uuid4().hex[:8]
    session = AnalysisSession(session_id, req.url, req.ai_mode, req.stream_frames)
    sessions[session_id] = session
    await db.create_session(session_id, req.url, req.ai_mode)
    session.task = asyncio.create_task(run_analysis(session))
    return {"status": "started", "url": req.url, "session_id": session_id}


@app.get("/")
async def health_check() -> dict:
    return {"status": "ok"}


# ─── Session & Event API ────────────────────────────────────────────────────

@app.get("/api/sessions")
async def list_sessions(limit: int = 50):
    return await db.get_sessions(limit)


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    session = await db.get_session(session_id)
    if not session:
        from fastapi import HTTPException
        raise HTTPException(404, "Session not found")
    return session


@app.get("/api/sessions/{session_id}/events")
async def get_session_events(session_id: str, event_type: str = None):
    return await db.get_events(session_id, event_type)


@app.get("/api/sessions/{session_id}/export")
async def export_events(session_id: str, format: str = "json"):
    if format == "csv":
        from fastapi.responses import PlainTextResponse
        csv_data = await db.export_events_csv(session_id)
        return PlainTextResponse(csv_data, media_type="text/csv")
    return await db.get_events(session_id)


@app.get("/api/sessions/{session_id}/indexer")
async def get_indexer_insights(session_id: str):
    """Return Video Indexer insights for an active session."""
    session = sessions.get(session_id)
    if session and session.indexer_insights and session.indexer_insights.ready:
        return {
            "ready": True,
            "scoreboard": session.indexer_insights.scoreboard,
            "transcript_segments": session.indexer_insights.transcript_segments[:50],
            "scenes": session.indexer_insights.scenes,
            "faces": [f["name"] for f in session.indexer_insights.faces],
        }
    return {"ready": False}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
                cmd = data.get("type")

                if cmd == "join":
                    sid = data.get("session_id")
                    if sid:
                        manager.join_session(websocket, sid)

                elif cmd == "stop":
                    session = manager.get_session(websocket)
                    if session:
                        await session.broadcast({"type": "status", "status": "stopped"})
                        session.cancel()
                        sessions.pop(session.id, None)
                        # Clear mapping for all clients of this session
                        for ws in list(session.clients):
                            manager.ws_session.pop(id(ws), None)

                elif cmd == "pause":
                    session = manager.get_session(websocket)
                    if session:
                        session.running.clear()
                        await session.broadcast({"type": "status", "status": "paused"})

                elif cmd == "resume":
                    session = manager.get_session(websocket)
                    if session:
                        session.running.set()
                        await session.broadcast({"type": "status", "status": "analyzing"})

                elif cmd == "seek":
                    session = manager.get_session(websocket)
                    if session and session.stream:
                        target_time = data.get("time", 0)
                        session.seek_target = int(target_time * session.stream.fps)

            except (json.JSONDecodeError, AttributeError):
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


# ─── Analysis loop ───────────────────────────────────────────────────────────

async def run_analysis(session: AnalysisSession) -> None:
    try:
        stream = VideoStream(session.url)
        session.stream = stream
        audio = AudioAnalyzer(session.url)
        session.audio = audio

        # Identify teams from first frame
        match_info = {}
        ret_first, first_frame = stream.read()
        if ret_first:
            stream.seek(0)
            resized_first = cv2.resize(first_frame, (640, 360))
            try:
                match_info = session.ai_service.identify_match(resized_first)
                await db.update_session_teams(
                    session.id,
                    match_info.get("home_team", "Home"),
                    match_info.get("away_team", "Away"),
                )
            except Exception:
                match_info = {"home_team": "Home", "away_team": "Away"}

        await session.broadcast({
            "type": "metadata",
            "api_version": API_VERSION,
            "fps": stream.fps,
            "total_frames": stream.total_frames,
            "duration": stream.duration,
            "has_audio": audio.available,
            "session_id": session.id,
            "match_info": match_info,
            "video_url": session.url,
            "stream_frames": session.stream_frames,
        })

        # Launch Video Indexer in background (non-blocking)
        indexer = VideoIndexerClient()
        if indexer.available:
            video_id = await indexer.submit_video(session.url, name=f"vio-{session.id}")
            if video_id:
                async def on_indexer_ready(insights: VideoIndexerInsights):
                    session.indexer_insights = insights
                    await session.broadcast({
                        "type": "indexer_ready",
                        "scoreboard": insights.scoreboard,
                        "scenes": insights.scenes,
                        "transcript_count": len(insights.transcript_segments),
                    })
                    if insights.scoreboard:
                        await db.save_event(session.id, {
                            "time_sec": 0,
                            "event_type": "scoreboard_ocr",
                            "description": (
                                f"Score: {insights.scoreboard.get('home_score', '?')}"
                                f"-{insights.scoreboard.get('away_score', '?')}"
                            ),
                            "tension_score": 0,
                        })
                session.indexer_task = asyncio.create_task(
                    indexer.poll_and_extract(video_id, on_ready=on_indexer_ready)
                )

        FRAME_INTERVAL = 30
        AI_INTERVAL = 3 if session.ai_service.mode != "local" else 5
        sampled = 0
        idx = 0

        while not session.cancelled:
            await session.running.wait()
            if session.cancelled:
                break

            # Handle pending seek
            if session.seek_target is not None:
                target = session.seek_target
                session.seek_target = None
                stream.seek(target)
                idx = target
                sampled = 0
                session.ai_service.clear_history()

            ret, frame = stream.read()
            if not ret:
                break

            if idx % FRAME_INTERVAL == 0:
                resized = cv2.resize(frame, (640, 360))

                time_sec = idx / stream.fps
                crowd = audio.get_crowd_intensity(time_sec)

                detections = detect_objects(resized)
                detections, team_colors = extract_team_colors(resized, detections)

                if sampled % AI_INTERVAL == 0:
                    # Feed transcript context from Video Indexer if available
                    transcript = None
                    if session.indexer_insights and session.indexer_insights.ready:
                        for seg in session.indexer_insights.transcript_segments:
                            if seg["start_sec"] <= time_sec <= seg["end_sec"]:
                                transcript = seg["text"]
                                break
                    event = session.ai_service.analyze(
                        resized, crowd_intensity=crowd, transcript=transcript
                    )
                else:
                    event = {"event_type": "normal_play", "tension_score": 0,
                             "description": None, "sentiment": "calm",
                             "model_source": session.ai_service.mode}

                # Multi-signal event confirmation
                event_type = event.get("event_type", "normal_play")
                tension = event.get("tension_score", 0)

                # Goal confirmation
                if event_type in ("goal", "celebration") and crowd >= 8.0 and tension >= 8:
                    event["event_type"] = "goal"
                    event["confirmed_goal"] = True

                # Card confirmation
                if event_type in ("yellow_card", "red_card") and tension >= 5:
                    event["confirmed_card"] = True

                # Penalty confirmation
                if event_type == "penalty" and tension >= 7:
                    event["confirmed_penalty"] = True

                payload = {
                    "type": "event",
                    "frame_index": idx,
                    "time_sec": time_sec,
                    "detections": detections,
                    "team_colors": team_colors,
                    "crowd_intensity": crowd,
                    **event,
                }
                if session.stream_frames:
                    frame_b64 = frame_to_jpeg_base64(resized)
                    payload["frame_data"] = f"data:image/jpeg;base64,{frame_b64}"

                # Smart poll evaluation
                poll = session.ai_service.poll_engine.evaluate(
                    event_type=event.get("event_type", "normal_play"),
                    time_sec=time_sec,
                    tension=event.get("tension_score", 0),
                    match_info=session.ai_service.match_info,
                )
                if poll:
                    payload["smart_poll"] = poll

                await session.broadcast(payload)

                # Persist non-trivial events to SQLite
                evt_type = event.get("event_type", "normal_play")
                if evt_type != "normal_play" or event.get("tension_score", 0) >= 3:
                    await db.save_event(session.id, {
                        "time_sec": time_sec,
                        "frame_index": idx,
                        "detections": detections,
                        "crowd_intensity": crowd,
                        **event,
                    })

                sampled += 1

            idx += 1
            await asyncio.sleep(0.01)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        await session.broadcast({"type": "error", "message": str(e)})
    finally:
        duration = session.stream.duration if session.stream else 0
        if session.stream:
            session.stream.release()
            session.stream = None
        await session.broadcast({"type": "status", "status": "finished"})
        await db.end_session(session.id, duration)
        sessions.pop(session.id, None)
