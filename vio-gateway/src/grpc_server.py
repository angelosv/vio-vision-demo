"""gRPC server exposing MatchEventsService for B2B clients (Viaplay/TV2).

3 RPCs:
  - StreamEvents(session_id)         — server-streaming; live push of MatchFrames
  - ListSessions()                   — from Postgres
  - GetSessionEvents(session_id,...) — server-streaming; historical events

The server runs alongside the FastAPI WebSocket gateway on port 50051.
Shares the same Redis + Postgres connections to avoid duplication.
"""

import asyncio
import json
import os
from typing import Optional

import grpc
import redis.asyncio as aioredis

from database import Database
from proto import match_events_pb2 as pb
from proto import match_events_pb2_grpc as pb_grpc


GRPC_PORT = int(os.getenv("GRPC_PORT", "50051"))


# ─── Conversion: JSON payload → protobuf MatchFrame ─────────────────────

def _json_to_match_frame(payload: dict) -> pb.MatchFrame:
    frame = pb.MatchFrame(
        session_id=str(payload.get("session_id", "")),
        timestamp_ms=int(payload.get("timestamp_ms", 0)),
        frame_time_sec=float(payload.get("frame_time_sec", 0)),
        frame_index=int(payload.get("frame_index", 0)),
        crowd_intensity=float(payload.get("crowd_intensity", -1)),
        sentiment=str(payload.get("sentiment") or ""),
    )

    # Tracks
    for t in payload.get("tracks", []) or []:
        pos = t.get("position", {}) or {}
        bbox = t.get("bbox", {}) or {}
        obj_type = t.get("type", "Player")
        object_type = pb.OBJECT_PLAYER
        if obj_type == "Goalkeeper" or obj_type == "GK":
            object_type = pb.OBJECT_GOALKEEPER
        elif obj_type == "Referee":
            object_type = pb.OBJECT_REFEREE

        frame.tracks.append(pb.TrackedObject(
            track_id=int(t.get("track_id") or 0),
            type=object_type,
            team=str(t.get("team", "")),
            jersey_number=str(t.get("jersey_number") or ""),
            position=pb.Position(x=float(pos.get("x", 0)), y=float(pos.get("y", 0))),
            bbox=pb.BoundingBox(
                x1=float(bbox.get("x1", 0)), y1=float(bbox.get("y1", 0)),
                x2=float(bbox.get("x2", 0)), y2=float(bbox.get("y2", 0)),
            ),
            confidence=float(t.get("confidence", 0)),
            team_color=str(t.get("team_color", "")),
        ))

    # Ball
    ball = payload.get("ball")
    if ball:
        pos = ball.get("position", {}) or {}
        bbox = ball.get("bbox", {}) or {}
        frame.ball.CopyFrom(pb.Ball(
            position=pb.Position(x=float(pos.get("x", 0)), y=float(pos.get("y", 0))),
            bbox=pb.BoundingBox(
                x1=float(bbox.get("x1", 0)), y1=float(bbox.get("y1", 0)),
                x2=float(bbox.get("x2", 0)), y2=float(bbox.get("y2", 0)),
            ),
            confidence=float(ball.get("confidence", 0)),
        ))

    # Possession
    poss = payload.get("possession")
    if poss and poss.get("team"):
        frame.possession.CopyFrom(pb.Possession(
            team=str(poss.get("team") or ""),
            home_percent=float(poss.get("home_percent", 0)),
            away_percent=float(poss.get("away_percent", 0)),
        ))

    # Event
    evt = payload.get("event")
    if evt:
        frame.event.CopyFrom(pb.MatchEvent(
            type=str(evt.get("event_type") or evt.get("type", "")),
            description=str(evt.get("description") or ""),
            tension_score=float(evt.get("tension_score", 0)),
            confirmed=bool(evt.get("confirmed", False)),
            event_id=int(evt.get("event_id", 0)),
        ))

    return frame


def _session_row_to_pb(row: dict) -> pb.Session:
    status_map = {
        "active": pb.STATUS_ACTIVE,
        "completed": pb.STATUS_COMPLETED,
        "failed": pb.STATUS_FAILED,
    }

    def _ts_ms(v) -> int:
        if v is None:
            return 0
        if hasattr(v, "timestamp"):
            return int(v.timestamp() * 1000)
        return 0

    return pb.Session(
        id=str(row.get("id", "")),
        url=str(row.get("url", "")),
        source_type=str(row.get("source_type") or ""),
        status=status_map.get(row.get("status"), pb.STATUS_UNSPECIFIED),
        home_team=str(row.get("home_team") or ""),
        away_team=str(row.get("away_team") or ""),
        start_time_ms=_ts_ms(row.get("start_time")),
        end_time_ms=_ts_ms(row.get("end_time")),
        duration_sec=float(row.get("duration_sec") or 0),
    )


def _event_row_to_frame(row: dict) -> pb.MatchFrame:
    """Historical event from Postgres → MatchFrame with just the event field."""
    frame = pb.MatchFrame(
        session_id=str(row.get("session_id", "")),
        frame_time_sec=float(row.get("time_sec", 0)),
        frame_index=int(row.get("frame_index", 0)),
        crowd_intensity=float(row.get("crowd_intensity", -1)),
        sentiment=str(row.get("sentiment") or ""),
    )
    frame.event.CopyFrom(pb.MatchEvent(
        type=str(row.get("event_type", "")),
        description=str(row.get("description") or ""),
        tension_score=float(row.get("tension_score", 0)),
        confirmed=bool(row.get("confirmed", False)),
    ))
    return frame


# ─── Servicer ───────────────────────────────────────────────────────────

class MatchEventsServicer(pb_grpc.MatchEventsServiceServicer):
    def __init__(self, redis_client: aioredis.Redis, db: Database):
        self.redis = redis_client
        self.db = db

    async def StreamEvents(self, request: pb.StreamEventsRequest, context):
        """Server-streaming: push live MatchFrames for a session."""
        session_id = request.session_id
        filters = set(request.event_type_filter)
        if not session_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "session_id required")
            return

        pubsub = self.redis.pubsub()
        await pubsub.subscribe(f"session:{session_id}:detections")
        try:
            async for msg in pubsub.listen():
                if msg["type"] != "message":
                    continue
                try:
                    data = json.loads(msg["data"])
                    # Apply filter
                    if filters:
                        evt_type = (data.get("event") or {}).get("event_type", "normal_play")
                        if evt_type not in filters:
                            continue
                    frame = _json_to_match_frame(data)
                    yield frame
                except Exception as e:
                    print(f"[grpc] stream error: {e}")
        finally:
            await pubsub.unsubscribe()

    async def ListSessions(self, request: pb.ListSessionsRequest, context):
        limit = request.limit if request.limit > 0 else 50
        offset = request.offset
        status_filter = None
        if request.status_filter == pb.STATUS_ACTIVE:
            status_filter = "active"
        elif request.status_filter == pb.STATUS_COMPLETED:
            status_filter = "completed"

        rows = await self.db.list_sessions(limit=limit, offset=offset, status=status_filter)
        resp = pb.ListSessionsResponse(total=len(rows))
        for r in rows:
            resp.sessions.append(_session_row_to_pb(r))
        return resp

    async def GetSessionEvents(self, request: pb.GetSessionEventsRequest, context):
        session_id = request.session_id
        start = request.start_time_sec if request.start_time_sec >= 0 else None
        end = request.end_time_sec if request.end_time_sec > 0 else None
        rows = await self.db.get_events(
            session_id, start_sec=start, end_sec=end, limit=100000,
        )
        for r in rows:
            yield _event_row_to_frame(r)


# ─── Lifecycle ──────────────────────────────────────────────────────────

async def serve_grpc(
    redis_client: aioredis.Redis,
    db: Database,
    port: int = GRPC_PORT,
) -> grpc.aio.Server:
    server = grpc.aio.server()
    pb_grpc.add_MatchEventsServiceServicer_to_server(
        MatchEventsServicer(redis_client, db), server,
    )
    bind = f"[::]:{port}"
    server.add_insecure_port(bind)
    await server.start()
    print(f"[grpc] serving MatchEventsService on {bind}")
    return server
