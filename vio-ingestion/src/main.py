"""vio-ingestion: multi-protocol video ingestion service.

Accepts SRT, RTMP, HLS, and MP4 sources, decodes frames, and publishes them
to Azure Redis Cache for downstream inference workers to consume.

Endpoints:
  POST /ingest        — start a new ingestion (source URL + protocol)
  POST /stop/{id}     — stop an active session
  GET  /sessions      — list active sessions
  GET  /health        — liveness probe
"""

import asyncio
import os
import uuid
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from decoder import VideoDecoder, detect_source_type
from redis_publisher import FramePublisher


app = FastAPI(title="Vio Vision Ingestion Service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class IngestRequest(BaseModel):
    url: str
    session_id: Optional[str] = None


class IngestSession:
    def __init__(self, session_id: str, url: str):
        self.id = session_id
        self.url = url
        self.source_type = detect_source_type(url)
        self.cancelled = False
        self.task: Optional[asyncio.Task] = None
        self.fps: float = 0
        self.frames_sent: int = 0


sessions: Dict[str, IngestSession] = {}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "vio-ingestion", "active_sessions": len(sessions)}


@app.post("/ingest")
async def ingest(req: IngestRequest) -> dict:
    """Start a new ingestion session."""
    session_id = req.session_id or uuid.uuid4().hex[:8]
    if session_id in sessions:
        raise HTTPException(409, f"Session {session_id} already running")

    session = IngestSession(session_id, req.url)
    sessions[session_id] = session
    session.task = asyncio.create_task(_run_ingest(session))

    return {
        "session_id": session_id,
        "source_type": session.source_type,
        "url": req.url,
        "status": "started",
    }


@app.post("/stop/{session_id}")
async def stop(session_id: str):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, f"Session {session_id} not found")
    session.cancelled = True
    if session.task and not session.task.done():
        session.task.cancel()
    sessions.pop(session_id, None)
    return {"session_id": session_id, "status": "stopped"}


@app.get("/sessions")
async def list_sessions():
    return {
        "active": [
            {
                "id": s.id,
                "url": s.url,
                "source_type": s.source_type,
                "fps": s.fps,
                "frames_sent": s.frames_sent,
            }
            for s in sessions.values()
        ]
    }


async def _run_ingest(session: IngestSession) -> None:
    """Background task: decode frames and publish to Redis."""
    publisher = FramePublisher(session.id)
    decoder: Optional[VideoDecoder] = None

    try:
        print(f"[{session.id}] opening {session.source_type}: {session.url}")
        decoder = VideoDecoder(session.url, target_size=(640, 360))
        info = decoder.info()
        session.fps = info.fps

        publisher.publish_metadata({
            "fps": info.fps,
            "width": info.width,
            "height": info.height,
            "total_frames": info.total_frames,
            "duration_sec": info.duration_sec,
            "source_type": info.source_type,
            "url": session.url,
        })
        print(f"[{session.id}] stream info: fps={info.fps}, {info.width}x{info.height}, "
              f"duration={info.duration_sec:.1f}s")

        def on_frame(frame, idx):
            publisher.publish(frame, idx, info.fps)
            session.frames_sent = idx + 1

        # Run decoder in thread executor since OpenCV is blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: decoder.read_frames(on_frame, lambda: session.cancelled),
        )

    except asyncio.CancelledError:
        print(f"[{session.id}] cancelled")
    except Exception as e:
        print(f"[{session.id}] error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if decoder:
            decoder.release()
        publisher.publish_end()
        publisher.close()
        sessions.pop(session.id, None)
        print(f"[{session.id}] ingestion finished (frames={session.frames_sent})")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8001"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
