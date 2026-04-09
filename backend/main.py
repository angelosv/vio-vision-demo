import asyncio
import json
import os
from typing import List

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass  # dotenv optional — env vars can be set externally

import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from stream_reader import frame_generator
from analyzer import ai_service, detect_objects, frame_to_jpeg_base64


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


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        dead: List[WebSocket] = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except WebSocketDisconnect:
                dead.append(connection)
            except Exception:
                dead.append(connection)
        for d in dead:
            self.disconnect(d)


manager = ConnectionManager()

# Controls whether the analysis loop is running or paused.
# Set = running, cleared = paused.
analysis_running = asyncio.Event()
analysis_running.set()  # start in "running" state


@app.get("/api")
async def root() -> dict:
    return {"status": "ok", "message": "Vio Vision Demo Backend"}


@app.post("/api/start")
async def start_demo(req: StartRequest) -> dict:
    """Start processing the given match URL in a background task."""

    # Set AI mode for this analysis session
    ai_service.mode = req.ai_mode
    # Ensure analysis is running (not paused from a previous session)
    analysis_running.set()

    async def run_analysis(url: str) -> None:
        try:
            FRAME_INTERVAL = 30   # ~1 frame/s at 30fps
            AI_INTERVAL = 3       # GPT-4o: every 3 sampled frames (~3s); Gemma: every 5
            if ai_service.mode == "local":
                AI_INTERVAL = 5
            sampled = 0
            idx = 0

            for frame in frame_generator(url):
                # Wait here if analysis is paused
                await analysis_running.wait()

                if idx % FRAME_INTERVAL == 0:
                    resized = cv2.resize(frame, (640, 360))

                    # ── YOLO (every frame, fast) ──
                    detections = detect_objects(resized)

                    # ── AI semantic analysis ──
                    if sampled % AI_INTERVAL == 0:
                        event = ai_service.analyze(resized)
                    else:
                        event = {"event_type": "normal_play", "tension_score": 0,
                                 "description": None, "sentiment": "calm",
                                 "model_source": ai_service.mode}

                    frame_b64 = frame_to_jpeg_base64(resized)

                    await manager.broadcast({
                        "type": "event",
                        "frame_index": idx,
                        "frame_data": f"data:image/jpeg;base64,{frame_b64}",
                        "detections": detections,
                        **event,
                    })

                    sampled += 1

                idx += 1
                await asyncio.sleep(0.01)

        except Exception as e:
            await manager.broadcast({"type": "error", "message": str(e)})

    asyncio.create_task(run_analysis(req.url))
    return {"status": "started", "url": req.url}


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
                if cmd == "pause":
                    analysis_running.clear()
                    await manager.broadcast({"type": "status", "status": "paused"})
                elif cmd == "resume":
                    analysis_running.set()
                    await manager.broadcast({"type": "status", "status": "analyzing"})
            except (json.JSONDecodeError, AttributeError):
                pass  # Ignore non-JSON or malformed messages
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
