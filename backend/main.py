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
from analyzer import AIService, detect_objects, frame_to_jpeg_base64


app = FastAPI(title="Vio Vision Demo Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartRequest(BaseModel):
    url: str
    ai_mode: str = "cloud"  # "cloud" (GPT-4o Azure) or "local" (Gemma)


# ─── Session management ─────────────────────────────────────────────────────

MAX_CONCURRENT_SESSIONS = 2  # Demo limit: max 2 concurrent analyses

class AnalysisSession:
    """Encapsulates all state for one analysis run, scoped to its clients."""

    def __init__(self, session_id: str, url: str, ai_mode: str):
        self.id = session_id
        self.url = url
        self.ai_service = AIService(mode=ai_mode)
        self.stream: Optional[VideoStream] = None
        self.audio: Optional[AudioAnalyzer] = None
        self.running = asyncio.Event()
        self.running.set()
        self.cancelled = False
        self.seek_target: Optional[int] = None
        self.task: Optional[asyncio.Task] = None
        self.clients: Set[WebSocket] = set()

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
    return {"status": "ok", "message": "Vio Vision Demo Backend"}


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
    session = AnalysisSession(session_id, req.url, req.ai_mode)
    sessions[session_id] = session
    session.task = asyncio.create_task(run_analysis(session))
    return {"status": "started", "url": req.url, "session_id": session_id}


@app.get("/")
async def health_check() -> dict:
    return {"status": "ok"}


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

        await session.broadcast({
            "type": "metadata",
            "fps": stream.fps,
            "total_frames": stream.total_frames,
            "duration": stream.duration,
            "has_audio": audio.available,
            "session_id": session.id,
        })

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

                if sampled % AI_INTERVAL == 0:
                    event = session.ai_service.analyze(resized, crowd_intensity=crowd)
                else:
                    event = {"event_type": "normal_play", "tension_score": 0,
                             "description": None, "sentiment": "calm",
                             "model_source": session.ai_service.mode}

                frame_b64 = frame_to_jpeg_base64(resized)

                await session.broadcast({
                    "type": "event",
                    "frame_index": idx,
                    "frame_data": f"data:image/jpeg;base64,{frame_b64}",
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
        if session.stream:
            session.stream.release()
            session.stream = None
        sessions.pop(session.id, None)
