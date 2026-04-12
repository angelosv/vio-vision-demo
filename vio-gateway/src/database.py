"""Postgres/TimescaleDB persistence for vio-gateway.

Stores:
  - sessions: one row per analysis run
  - events: hypertable of detected events (high volume)

Uses asyncpg for async access. Gracefully falls back to plain Postgres
if TimescaleDB hypertable creation fails.
"""

import asyncio
import csv
import io
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg


POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql://vio:vio@localhost:5432/vio_vision",
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    source_type TEXT,
    home_team TEXT,
    away_team TEXT,
    start_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    end_time TIMESTAMPTZ,
    duration_sec REAL,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL,
    session_id TEXT NOT NULL,
    frame_index INTEGER NOT NULL,
    time_sec REAL NOT NULL,
    event_type TEXT NOT NULL,
    tension_score REAL DEFAULT 0,
    description TEXT,
    sentiment TEXT,
    crowd_intensity REAL DEFAULT -1,
    detections_json JSONB,
    confirmed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, created_at)
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(session_id, time_sec);
"""


class Database:
    MAX_INIT_ATTEMPTS = 5
    INIT_BACKOFF_SEC = 2.0

    def __init__(self, dsn: str = POSTGRES_URL):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def init(self) -> None:
        """Create pool + schema with retry for Postgres startup race."""
        last_err: Optional[Exception] = None
        for attempt in range(1, self.MAX_INIT_ATTEMPTS + 1):
            try:
                self.pool = await asyncpg.create_pool(
                    self.dsn,
                    min_size=1,
                    max_size=10,
                    command_timeout=30,
                )
                break
            except Exception as e:
                last_err = e
                print(f"[db] connect attempt {attempt}/{self.MAX_INIT_ATTEMPTS} failed: {e}")
                if attempt < self.MAX_INIT_ATTEMPTS:
                    await asyncio.sleep(self.INIT_BACKOFF_SEC)
        if not self.pool:
            raise RuntimeError(f"Could not connect to Postgres: {last_err}")

        async with self.pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)
            # Try to convert events to a TimescaleDB hypertable
            try:
                await conn.execute(
                    "SELECT create_hypertable('events', 'created_at', "
                    "if_not_exists => TRUE, migrate_data => TRUE)"
                )
                print("[db] TimescaleDB hypertable enabled for events")
            except Exception as e:
                print(f"[db] hypertable skipped (plain Postgres ok): {e}")

        print(f"[db] connected to {self.dsn.split('@')[-1]}")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    # ── Sessions ──────────────────────────────────────────────────────────

    async def create_session(
        self,
        session_id: str,
        url: str,
        source_type: Optional[str] = None,
        home_team: Optional[str] = None,
        away_team: Optional[str] = None,
    ) -> None:
        assert self.pool
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO sessions (id, url, source_type, home_team, away_team, status)
                   VALUES ($1, $2, $3, $4, $5, 'active')
                   ON CONFLICT (id) DO NOTHING""",
                session_id, url, source_type, home_team, away_team,
            )

    async def update_session_teams(self, session_id: str, home: str, away: str) -> None:
        assert self.pool
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE sessions SET home_team=$1, away_team=$2 WHERE id=$3",
                home, away, session_id,
            )

    async def end_session(self, session_id: str, duration_sec: float = 0) -> None:
        assert self.pool
        async with self.pool.acquire() as conn:
            await conn.execute(
                """UPDATE sessions
                   SET end_time = NOW(), duration_sec = $2, status = 'completed'
                   WHERE id = $1""",
                session_id, duration_sec,
            )

    async def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        assert self.pool
        async with self.pool.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    "SELECT * FROM sessions WHERE status=$1 "
                    "ORDER BY start_time DESC LIMIT $2 OFFSET $3",
                    status, limit, offset,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM sessions ORDER BY start_time DESC LIMIT $1 OFFSET $2",
                    limit, offset,
                )
            return [dict(r) for r in rows]

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        assert self.pool
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM sessions WHERE id=$1", session_id)
            return dict(row) if row else None

    # ── Events ────────────────────────────────────────────────────────────

    async def save_event(self, session_id: str, event: Dict[str, Any]) -> None:
        """Save a detected event. Fire-and-forget friendly (no return)."""
        if not self.pool:
            return
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO events
                       (session_id, frame_index, time_sec, event_type, tension_score,
                        description, sentiment, crowd_intensity, detections_json, confirmed)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                    session_id,
                    int(event.get("frame_index", 0)),
                    float(event.get("time_sec", 0)),
                    event.get("event_type", "normal_play"),
                    float(event.get("tension_score", 0)),
                    event.get("description"),
                    event.get("sentiment"),
                    float(event.get("crowd_intensity", -1)),
                    json.dumps(event.get("detections", [])),
                    bool(event.get("confirmed", False)),
                )
        except Exception as e:
            # Don't crash the broadcast loop over a persistence hiccup
            print(f"[db] save_event error: {e}")

    async def get_events(
        self,
        session_id: str,
        event_type: Optional[str] = None,
        start_sec: Optional[float] = None,
        end_sec: Optional[float] = None,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        assert self.pool
        clauses = ["session_id = $1"]
        params: List[Any] = [session_id]
        if event_type:
            clauses.append(f"event_type = ${len(params) + 1}")
            params.append(event_type)
        if start_sec is not None:
            clauses.append(f"time_sec >= ${len(params) + 1}")
            params.append(start_sec)
        if end_sec is not None:
            clauses.append(f"time_sec <= ${len(params) + 1}")
            params.append(end_sec)
        params.append(limit)
        sql = (
            f"SELECT * FROM events WHERE {' AND '.join(clauses)} "
            f"ORDER BY time_sec ASC LIMIT ${len(params)}"
        )
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [dict(r) for r in rows]

    async def export_csv(self, session_id: str) -> str:
        events = await self.get_events(session_id, limit=1000000)
        if not events:
            return ""
        output = io.StringIO()
        # Remove non-serializable fields
        for ev in events:
            if isinstance(ev.get("created_at"), datetime):
                ev["created_at"] = ev["created_at"].isoformat()
            if isinstance(ev.get("detections_json"), dict) or isinstance(ev.get("detections_json"), list):
                ev["detections_json"] = json.dumps(ev["detections_json"])
        writer = csv.DictWriter(output, fieldnames=events[0].keys())
        writer.writeheader()
        writer.writerows(events)
        return output.getvalue()
