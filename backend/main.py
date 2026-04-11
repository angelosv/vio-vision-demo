import asyncio
import json
import os
import time
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
from analyzer import AIService, PlayerTracker, detect_objects, extract_team_colors, frame_to_jpeg_base64
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


async def cleanup_old_sessions():
    """Background task to cleanup abandoned sessions older than SESSION_TIMEOUT."""
    while True:
        await asyncio.sleep(300)  # Check every 5 minutes
        current_time = time.time()
        for session_id, session in list(sessions.items()):
            age = current_time - session.created_at
            if age > SESSION_TIMEOUT:
                print(f"[Cleanup] Removing stale session {session_id} (age: {age:.0f}s)")
                session.cancel()
                sessions.pop(session_id, None)


@app.on_event("startup")
async def startup():
    await db.init()
    # Start background cleanup task
    asyncio.create_task(cleanup_old_sessions())
    print("[Startup] Background session cleanup task started")


@app.on_event("shutdown")
async def shutdown():
    await db.close()


class StartRequest(BaseModel):
    url: str
    ai_mode: str = "cloud"  # "cloud" (GPT-4o Azure) or "local" (Gemma)
    stream_frames: bool = False  # when False, use native playback with detection coordinates (MUCH faster for remote videos)


class StopRequest(BaseModel):
    session_id: str


# ─── Session management ─────────────────────────────────────────────────────

MAX_CONCURRENT_SESSIONS = 2  # Demo limit: max 2 concurrent analyses
SESSION_TIMEOUT = 3600  # Auto-cleanup sessions after 1 hour of inactivity

class AnalysisSession:
    """Encapsulates all state for one analysis run, scoped to its clients."""

    def __init__(self, session_id: str, url: str, ai_mode: str, stream_frames: bool = False):
        self.id = session_id
        self.url = url
        self.stream_frames = stream_frames
        self.created_at = time.time()  # Timestamp for session cleanup
        self.ai_service = AIService(mode=ai_mode)
        self.player_tracker = PlayerTracker()
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
        msg_type = message.get("type", "unknown")
        # Only log metadata and status messages to avoid spam
        if msg_type in ["metadata", "status"]:
            print(f"[WS-Backend] Broadcasting {msg_type} to {len(self.clients)} client(s)")
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
        print(f"[WS-Backend] New WebSocket connection accepted. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        # Remove from its session; cancel session if no clients left
        session_id = self.ws_session.pop(id(websocket), None)
        print(f"[WS-Backend] WebSocket disconnected. Total connections: {len(self.active_connections)}")
        if session_id:
            print(f"[WS-Backend] Was in session: {session_id}")
        if session_id and session_id in sessions:
            session = sessions[session_id]
            session.clients.discard(websocket)
            print(f"[WS-Backend] Session {session_id} now has {len(session.clients)} client(s)")
            if not session.clients:
                print(f"[WS-Backend] No more clients in session {session_id}, cancelling")
                session.cancel()
                sessions.pop(session_id, None)

    def join_session(self, websocket: WebSocket, session_id: str) -> None:
        # Leave previous session if any
        old_id = self.ws_session.get(id(websocket))
        print(f"[WS-Backend] WebSocket joining session: {session_id}")
        if old_id and old_id != session_id and old_id in sessions:
            print(f"[WS-Backend] Leaving old session: {old_id}")
            old_session = sessions[old_id]
            old_session.clients.discard(websocket)
            if not old_session.clients:
                old_session.cancel()
                sessions.pop(old_id, None)
        # Join new session
        self.ws_session[id(websocket)] = session_id
        if session_id in sessions:
            sessions[session_id].clients.add(websocket)
            print(f"[WS-Backend] Added to existing session {session_id}, total clients: {len(sessions[session_id].clients)}")
        else:
            print(f"[WS-Backend] Session {session_id} not found, client added to mapping but session doesn't exist yet")

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
    print(f"[API] POST /api/start - url: {req.url}, ai_mode: {req.ai_mode}, stream_frames: {req.stream_frames}")
    # Enforce concurrent session limit
    if len(sessions) >= MAX_CONCURRENT_SESSIONS:
        print(f"[API] Max concurrent sessions reached ({len(sessions)}/{MAX_CONCURRENT_SESSIONS})")
        raise HTTPException(
            status_code=429,
            detail=f"Maximum concurrent sessions ({MAX_CONCURRENT_SESSIONS}) reached. Please wait for other analyses to complete."
        )

    session_id = uuid.uuid4().hex[:8]
    print(f"[API] Creating new session: {session_id}")
    session = AnalysisSession(session_id, req.url, req.ai_mode, req.stream_frames)
    sessions[session_id] = session
    await db.create_session(session_id, req.url, req.ai_mode)
    session.task = asyncio.create_task(run_analysis(session))
    print(f"[API] Session {session_id} created and analysis started. Total sessions: {len(sessions)}")
    return {"status": "started", "url": req.url, "session_id": session_id}


@app.post("/api/stop")
async def stop_session(req: StopRequest) -> dict:
    """Stop and cleanup a session via HTTP (guaranteed synchronous cleanup)."""
    print(f"[API] POST /api/stop - session_id: {req.session_id}")
    session = sessions.get(req.session_id)
    if session:
        print(f"[API] Stopping session: {req.session_id}")
        # Broadcast stop message to all clients
        await session.broadcast({"type": "status", "status": "stopped"})
        # Cancel the session
        session.cancel()
        # Remove from active sessions
        sessions.pop(req.session_id, None)
        # Clear WebSocket mappings for all clients
        for ws in list(session.clients):
            manager.ws_session.pop(id(ws), None)
        print(f"[API] Session {req.session_id} stopped. Total sessions: {len(sessions)}")
        return {"status": "stopped", "session_id": req.session_id}
    else:
        print(f"[API] Session {req.session_id} not found")
        return {"status": "not_found", "session_id": req.session_id}


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
                print(f"[WS-Backend] Received message type: {cmd}")

                if cmd == "join":
                    sid = data.get("session_id")
                    if sid:
                        print(f"[WS-Backend] Client joining session: {sid}")
                        manager.join_session(websocket, sid)
                    else:
                        print(f"[WS-Backend] Join command without session_id")

                elif cmd == "stop":
                    session = manager.get_session(websocket)
                    if session:
                        print(f"[WS-Backend] Stopping session: {session.id}")
                        await session.broadcast({"type": "status", "status": "stopped"})
                        session.cancel()
                        sessions.pop(session.id, None)
                        # Clear mapping for all clients of this session
                        for ws in list(session.clients):
                            manager.ws_session.pop(id(ws), None)
                    else:
                        print(f"[WS-Backend] Stop command but no active session")

                elif cmd == "pause":
                    session = manager.get_session(websocket)
                    if session:
                        print(f"[WS-Backend] Pausing session: {session.id}")
                        session.running.clear()
                        await session.broadcast({"type": "status", "status": "paused"})

                elif cmd == "resume":
                    session = manager.get_session(websocket)
                    if session:
                        print(f"[WS-Backend] Resuming session: {session.id}")
                        session.running.set()
                        await session.broadcast({"type": "status", "status": "analyzing"})

                elif cmd == "seek":
                    session = manager.get_session(websocket)
                    if session and session.stream:
                        target_time = data.get("time", 0)
                        session.seek_target = int(target_time * session.stream.fps)
                        print(f"[WS-Backend] Seeking to {target_time}s in session: {session.id}")

            except (json.JSONDecodeError, AttributeError) as e:
                print(f"[WS-Backend] Error parsing message: {e}")
    except WebSocketDisconnect:
        print(f"[WS-Backend] WebSocket disconnected")
        manager.disconnect(websocket)
    except Exception as e:
        print(f"[WS-Backend] WebSocket exception: {e}")
        manager.disconnect(websocket)


# ─── Analysis loop ───────────────────────────────────────────────────────────

async def run_analysis(session: AnalysisSession) -> None:
    try:
        print(f"[Analysis] Starting analysis for session {session.id}, URL: {session.url}")
        print(f"[Analysis] Creating VideoStream...")
        stream = VideoStream(session.url)
        session.stream = stream
        print(f"[Analysis] VideoStream created. FPS: {stream.fps}, Duration: {stream.duration}s")

        print(f"[Analysis] Creating AudioAnalyzer...")
        audio = AudioAnalyzer(session.url)
        session.audio = audio
        print(f"[Analysis] AudioAnalyzer created. Audio available: {audio.available}")

        # Identify teams from first frame
        match_info = {}
        print(f"[Analysis] Reading first frame to identify teams...")
        ret_first, first_frame = stream.read()
        if ret_first:
            stream.seek(0)
            resized_first = cv2.resize(first_frame, (640, 360))
            try:
                print(f"[Analysis] Calling AI service to identify match (with 10s timeout)...")
                # Use asyncio.wait_for to add timeout to the blocking call
                match_info = await asyncio.wait_for(
                    asyncio.to_thread(session.ai_service.identify_match, resized_first),
                    timeout=10.0
                )
                print(f"[Analysis] Match identified: {match_info.get('home_team')} vs {match_info.get('away_team')}")
                await db.update_session_teams(
                    session.id,
                    match_info.get("home_team", "Home"),
                    match_info.get("away_team", "Away"),
                )
            except asyncio.TimeoutError:
                print(f"[Analysis] Match identification timed out after 10s, using defaults")
                match_info = {"home_team": "Home", "away_team": "Away"}
            except Exception as e:
                print(f"[Analysis] Failed to identify match: {e}")
                match_info = {"home_team": "Home", "away_team": "Away"}
        else:
            print(f"[Analysis] WARNING: Could not read first frame")
            match_info = {"home_team": "Home", "away_team": "Away"}

        print(f"[Analysis] Broadcasting metadata to {len(session.clients)} client(s)...")
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
        print(f"[Analysis] Metadata broadcasted successfully")

        # Launch Video Indexer in background (non-blocking)
        print(f"[Analysis] Initializing Video Indexer...")
        indexer = VideoIndexerClient()
        if indexer.available:
            print(f"[Analysis] Video Indexer is available, submitting video...")
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

        print(f"[Analysis] Starting main analysis loop (FRAME_INTERVAL={FRAME_INTERVAL}, AI_INTERVAL={AI_INTERVAL})")
        while not session.cancelled:
            print(f"[Analysis] Loop iteration {idx}, waiting for running event...")
            await session.running.wait()
            if session.cancelled:
                print(f"[Analysis] Session cancelled, exiting loop")
                break

            # Handle pending seek
            if session.seek_target is not None:
                target = session.seek_target
                session.seek_target = None
                stream.seek(target)
                idx = target
                sampled = 0
                session.ai_service.clear_history()
                session.player_tracker.reset()
                print(f"[Analysis] Seeked to frame {target}")

            print(f"[Analysis] Reading frame {idx}...")
            ret, frame = stream.read()
            if not ret:
                print(f"[Analysis] Failed to read frame {idx}, ending loop")
                break
            print(f"[Analysis] Frame {idx} read successfully")

            if idx % FRAME_INTERVAL == 0:
                print(f"[Analysis] Processing frame {idx} (sampled frame {sampled})")
                print(f"[Analysis] Resizing frame...")
                resized = cv2.resize(frame, (640, 360))
                print(f"[Analysis] Frame resized to 640x360")

                time_sec = idx / stream.fps
                print(f"[Analysis] Getting crowd intensity at {time_sec:.2f}s...")
                crowd = audio.get_crowd_intensity(time_sec)
                print(f"[Analysis] Crowd intensity: {crowd:.2f}")

                print(f"[Analysis] Running YOLO object detection...")
                detections = detect_objects(resized)
                print(f"[Analysis] YOLO detected {len(detections)} objects")

                print(f"[Analysis] Extracting team colors...")
                detections, team_colors = extract_team_colors(resized, detections)
                print(f"[Analysis] Team colors extracted: {team_colors}")

                print(f"[Analysis] Updating player tracker...")
                detections = session.player_tracker.update(detections)
                print(f"[Analysis] Player tracker updated")

                if sampled % AI_INTERVAL == 0:
                    print(f"[Analysis] Running AI analysis (sampled={sampled}, AI_INTERVAL={AI_INTERVAL})...")
                    # Feed transcript context from Video Indexer if available
                    transcript = None
                    if session.indexer_insights and session.indexer_insights.ready:
                        for seg in session.indexer_insights.transcript_segments:
                            if seg["start_sec"] <= time_sec <= seg["end_sec"]:
                                transcript = seg["text"]
                                break
                    try:
                        # Use asyncio.wait_for to add timeout to the blocking AI call
                        event = await asyncio.wait_for(
                            asyncio.to_thread(
                                session.ai_service.analyze,
                                resized,
                                crowd_intensity=crowd,
                                transcript=transcript
                            ),
                            timeout=15.0  # 15 second timeout for AI analysis
                        )
                        print(f"[Analysis] AI analysis complete: event_type={event.get('event_type')}, tension={event.get('tension_score')}")
                    except asyncio.TimeoutError:
                        print(f"[Analysis] AI analysis timed out after 15s, using default event")
                        event = {"event_type": "normal_play", "tension_score": 0,
                                 "description": None, "sentiment": "calm",
                                 "model_source": session.ai_service.mode}
                    except Exception as e:
                        print(f"[Analysis] AI analysis failed: {e}, using default event")
                        event = {"event_type": "normal_play", "tension_score": 0,
                                 "description": None, "sentiment": "calm",
                                 "model_source": session.ai_service.mode}
                else:
                    event = {"event_type": "normal_play", "tension_score": 0,
                             "description": None, "sentiment": "calm",
                             "model_source": session.ai_service.mode}
                    print(f"[Analysis] Skipping AI analysis (not AI interval)")

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

                print(f"[Analysis] Building event payload...")
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
                    print(f"[Analysis] Encoding frame to base64...")
                    frame_b64 = frame_to_jpeg_base64(resized)
                    payload["frame_data"] = f"data:image/jpeg;base64,{frame_b64}"

                # Smart poll evaluation
                print(f"[Analysis] Evaluating smart poll...")
                poll = session.ai_service.poll_engine.evaluate(
                    event_type=event.get("event_type", "normal_play"),
                    time_sec=time_sec,
                    tension=event.get("tension_score", 0),
                    match_info=session.ai_service.match_info,
                )
                if poll:
                    payload["smart_poll"] = poll
                    print(f"[Analysis] Smart poll generated: {poll.get('question', 'N/A')}")

                print(f"[Analysis] Broadcasting event to {len(session.clients)} client(s)...")
                await session.broadcast(payload)
                print(f"[Analysis] Event broadcasted successfully")

                # Persist non-trivial events to SQLite
                evt_type = event.get("event_type", "normal_play")
                if evt_type != "normal_play" or event.get("tension_score", 0) >= 3:
                    print(f"[Analysis] Saving event to database (type={evt_type}, tension={event.get('tension_score', 0)})...")
                    await db.save_event(session.id, {
                        "time_sec": time_sec,
                        "frame_index": idx,
                        "detections": detections,
                        "crowd_intensity": crowd,
                        **event,
                    })
                    print(f"[Analysis] Event saved to database")

                sampled += 1
                print(f"[Analysis] Frame {idx} processing complete (sampled={sampled})")

            idx += 1
            print(f"[Analysis] Sleeping 0.01s before next iteration...")
            await asyncio.sleep(0.01)

    except asyncio.CancelledError:
        print(f"[Analysis] Session {session.id} was cancelled")
    except Exception as e:
        print(f"[Analysis] ERROR in session {session.id}: {e}")
        import traceback
        traceback.print_exc()
        await session.broadcast({"type": "error", "message": str(e)})
    finally:
        print(f"[Analysis] Cleaning up session {session.id}...")
        duration = session.stream.duration if session.stream else 0
        if session.stream:
            session.stream.release()
            session.stream = None
        await session.broadcast({"type": "status", "status": "finished"})
        await db.end_session(session.id, duration)
        sessions.pop(session.id, None)
        print(f"[Analysis] Session {session.id} cleanup complete. Total sessions: {len(sessions)}")
