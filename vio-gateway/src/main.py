"""vio-gateway: fan-out service for live detections.

Consumes detection events from Redis pub/sub and delivers them to:
  - WebSocket clients (web frontend)
  - gRPC streaming clients (Viaplay/TV2 integrations — future)
  - REST API for historical queries (Postgres)

Also orchestrates the end-to-end session lifecycle by calling vio-ingestion.
"""

import asyncio
import json
import os
import uuid
from typing import Dict, List, Optional, Set

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from database import Database
from grpc_server import serve_grpc


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
INGESTION_URL = os.getenv("INGESTION_URL", "http://localhost:8001")

API_VERSION = "0.5.0"


app = FastAPI(title="Vio Vision Gateway", version=API_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Connection manager: WebSocket fan-out per session ──────────────────
class ConnectionManager:
    def __init__(self):
        # session_id -> set of WebSocket connections
        self.sessions: Dict[str, Set[WebSocket]] = {}
        # session_id -> background task reading Redis
        self.listeners: Dict[str, asyncio.Task] = {}

    async def connect(self, ws: WebSocket, session_id: str) -> None:
        await ws.accept()
        self.sessions.setdefault(session_id, set()).add(ws)
        if session_id not in self.listeners:
            self.listeners[session_id] = asyncio.create_task(
                self._listen(session_id)
            )

    def disconnect(self, ws: WebSocket, session_id: str) -> None:
        clients = self.sessions.get(session_id)
        if clients:
            clients.discard(ws)
            if not clients:
                self.sessions.pop(session_id, None)
                task = self.listeners.pop(session_id, None)
                if task:
                    task.cancel()

    async def broadcast(self, session_id: str, message: dict) -> None:
        clients = self.sessions.get(session_id, set())
        dead: List[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws, session_id)

    async def _listen(self, session_id: str) -> None:
        """Background task: read session's detection channel from Redis.

        Also subscribes to the `frames` channel to forward metadata/end events.
        Converts the new 'tracks' format into the legacy 'event' payload so
        the existing frontend works unchanged.
        """
        assert redis_client is not None
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(
            f"session:{session_id}:detections",
            f"session:{session_id}:frames",
        )
        try:
            async for msg in pubsub.listen():
                if msg["type"] != "message":
                    continue
                try:
                    channel = msg["channel"].decode() if isinstance(msg["channel"], bytes) else msg["channel"]
                    data = json.loads(msg["data"])

                    if channel.endswith(":frames"):
                        # Forward metadata / end events to clients
                        if data.get("type") == "metadata":
                            # Persist session row
                            asyncio.create_task(db.create_session(
                                session_id=session_id,
                                url=data.get("url", ""),
                                source_type=data.get("source_type"),
                            ))
                            await self.broadcast(session_id, {
                                "type": "metadata",
                                "api_version": API_VERSION,
                                "session_id": session_id,
                                "fps": data.get("fps", 25),
                                "duration": data.get("duration_sec", 0),
                                "total_frames": data.get("total_frames", 0),
                                "video_url": data.get("url"),
                                "source_type": data.get("source_type"),
                                "stream_frames": False,
                                "match_info": {},
                            })
                        elif data.get("type") == "end":
                            asyncio.create_task(db.end_session(
                                session_id=session_id,
                                duration_sec=data.get("duration_sec", 0),
                            ))
                            await self.broadcast(session_id, {
                                "type": "status",
                                "status": "finished",
                            })
                    else:
                        # Detection payload — transform for legacy frontend
                        legacy = _convert_detection_payload(data)
                        # Persist events that matter (non-normal_play or high tension)
                        evt_type = legacy.get("event_type", "normal_play")
                        tension = legacy.get("tension_score", 0) or 0
                        if evt_type != "normal_play" or tension >= 3:
                            asyncio.create_task(db.save_event(session_id, legacy))
                        await self.broadcast(session_id, legacy)
                except Exception as e:
                    print(f"[gateway] broadcast error: {e}")
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe()


def _convert_detection_payload(data: dict) -> dict:
    """Transform the new microservice payload into the legacy 'event' format.

    Keeps the existing frontend working while we migrate to the new schema.
    """
    tracks = data.get("tracks", [])
    ball = data.get("ball")

    # Build legacy 'detections' list (flat array with label/box)
    detections = []
    for t in tracks:
        bbox = t.get("bbox", {})
        label = t.get("type", "Player")
        jersey = t.get("jersey_number", "")
        detections.append({
            "label": label,
            "confidence": t.get("confidence", 0),
            "box": [bbox.get("x1", 0), bbox.get("y1", 0), bbox.get("x2", 0), bbox.get("y2", 0)],
            "color": t.get("team_color", ""),
            "team": 0 if t.get("team") == "home" else (1 if t.get("team") == "away" else -1),
            "player_id": t.get("track_id"),
            "jersey_number": jersey,
        })
    if ball:
        bbox = ball.get("bbox", {})
        detections.append({
            "label": "Ball",
            "confidence": ball.get("confidence", 0),
            "box": [bbox.get("x1", 0), bbox.get("y1", 0), bbox.get("x2", 0), bbox.get("y2", 0)],
        })

    return {
        "type": "event",
        "session_id": data.get("session_id"),
        "frame_index": data.get("frame_index", 0),
        "time_sec": data.get("frame_time_sec", 0),
        "detections": detections,
        "team_colors": data.get("team_colors", []),
        "crowd_intensity": data.get("crowd_intensity", -1),
        "event_type": data.get("event", {}).get("type", "normal_play") if data.get("event") else "normal_play",
        "tension_score": data.get("event", {}).get("tension_score", 0) if data.get("event") else 0,
        "description": data.get("event", {}).get("description") if data.get("event") else None,
        "sentiment": data.get("sentiment"),
    }


manager = ConnectionManager()
redis_client: Optional[aioredis.Redis] = None
db = Database()


@app.on_event("startup")
async def startup():
    global redis_client
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=False)
    print(f"[gateway] connected to Redis: {REDIS_URL}")
    print(f"[gateway] ingestion service: {INGESTION_URL}")
    await db.init()
    # Start gRPC server in parallel (non-blocking)
    app.state.grpc_server = await serve_grpc(redis_client, db)


@app.on_event("shutdown")
async def shutdown():
    grpc_server = getattr(app.state, "grpc_server", None)
    if grpc_server:
        await grpc_server.stop(grace=2)
    if redis_client:
        await redis_client.close()
    await db.close()


# ── REST API ────────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    url: str
    session_id: Optional[str] = None


@app.get("/")
async def health():
    return {
        "status": "ok",
        "service": "vio-gateway",
        "api_version": API_VERSION,
        "active_sessions": len(manager.sessions),
    }


@app.get("/api")
async def api_info():
    return {
        "status": "ok",
        "service": "vio-gateway",
        "api_version": API_VERSION,
    }


@app.post("/api/start")
async def start(req: StartRequest):
    """Start a new analysis session via the ingestion service."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.post(f"{INGESTION_URL}/ingest", json=req.dict())
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Ingestion service error: {e}")


@app.post("/api/stop")
async def stop_all():
    """Stop all sessions."""
    stopped = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"{INGESTION_URL}/sessions")
            r.raise_for_status()
            for s in r.json().get("active", []):
                await client.post(f"{INGESTION_URL}/stop/{s['id']}")
                stopped.append(s["id"])
        except httpx.HTTPError:
            pass
    return {"status": "stopped", "sessions": stopped}


@app.post("/api/stop/{session_id}")
async def stop(session_id: str):
    """Stop a specific session."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.post(f"{INGESTION_URL}/stop/{session_id}")
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Ingestion service error: {e}")


@app.get("/api/sessions/active")
async def list_active_sessions():
    """List currently-running sessions (from ingestion service)."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"{INGESTION_URL}/sessions")
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Ingestion service error: {e}")


@app.get("/api/sessions")
async def list_sessions(limit: int = 50, offset: int = 0, status: Optional[str] = None):
    """List session history from Postgres."""
    rows = await db.list_sessions(limit=limit, offset=offset, status=status)
    # Convert datetime to isoformat for JSON
    for r in rows:
        for k, v in list(r.items()):
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return rows


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    row = await db.get_session(session_id)
    if not row:
        raise HTTPException(404, "Session not found")
    for k, v in list(row.items()):
        if hasattr(v, "isoformat"):
            row[k] = v.isoformat()
    return row


@app.get("/api/sessions/{session_id}/events")
async def get_session_events(
    session_id: str,
    event_type: Optional[str] = None,
    start_sec: Optional[float] = None,
    end_sec: Optional[float] = None,
    limit: int = 10000,
):
    events = await db.get_events(session_id, event_type, start_sec, end_sec, limit)
    for e in events:
        for k, v in list(e.items()):
            if hasattr(v, "isoformat"):
                e[k] = v.isoformat()
    return events


@app.get("/api/sessions/{session_id}/export")
async def export_session(session_id: str, format: str = "json"):
    if format == "csv":
        csv_data = await db.export_csv(session_id)
        return PlainTextResponse(csv_data, media_type="text/csv")
    events = await db.get_events(session_id, limit=1000000)
    for e in events:
        for k, v in list(e.items()):
            if hasattr(v, "isoformat"):
                e[k] = v.isoformat()
    return events


# ── WebSocket endpoint ───────────────────────────────────────────────────

@app.websocket("/ws/live/{session_id}")
async def websocket_live(ws: WebSocket, session_id: str):
    """Stream live detections for a given session."""
    await manager.connect(ws, session_id)
    try:
        # Send initial hello
        await ws.send_json({
            "type": "connected",
            "session_id": session_id,
            "api_version": API_VERSION,
        })
        # Keep connection open; we push messages from Redis listener
        while True:
            # Handle client-sent messages (ping, commands)
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send keepalive
                await ws.send_json({"type": "ping"})
                continue
            # Could handle client commands here (seek, pause, etc.)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws, session_id)


# Legacy WS endpoint for backwards compat with the existing frontend
@app.websocket("/ws")
async def websocket_legacy(ws: WebSocket):
    """Legacy WebSocket that joins a session via 'join' command.

    Kept for compatibility with the current frontend. Future clients should
    use /ws/live/{session_id} directly.
    """
    await ws.accept()
    session_id: Optional[str] = None
    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
                cmd = data.get("type")
                if cmd == "join":
                    new_sid = data.get("session_id")
                    if new_sid and new_sid != session_id:
                        if session_id:
                            manager.disconnect(ws, session_id)
                        session_id = new_sid
                        manager.sessions.setdefault(session_id, set()).add(ws)
                        if session_id not in manager.listeners:
                            manager.listeners[session_id] = asyncio.create_task(
                                manager._listen(session_id)
                            )
                        await ws.send_json({
                            "type": "connected",
                            "session_id": session_id,
                        })
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        if session_id:
            manager.disconnect(ws, session_id)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
