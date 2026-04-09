"""SQLite persistence for analysis sessions and detected events.

Uses aiosqlite for async compatibility with FastAPI/asyncio.
Database file is stored locally at ./vio_vision.db.
"""

import csv
import io
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiosqlite

DB_PATH = os.getenv("VIO_DB_PATH", os.path.join(os.path.dirname(__file__), "vio_vision.db"))


class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self):
        """Create tables if they don't exist."""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                ai_mode TEXT NOT NULL DEFAULT 'cloud',
                home_team TEXT,
                away_team TEXT,
                start_time TEXT NOT NULL,
                end_time TEXT,
                duration REAL,
                status TEXT NOT NULL DEFAULT 'active'
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                time_sec REAL NOT NULL,
                frame_index INTEGER,
                event_type TEXT NOT NULL,
                tension_score REAL DEFAULT 0,
                description TEXT,
                sentiment TEXT,
                crowd_intensity REAL DEFAULT -1,
                detections_json TEXT,
                confirmed INTEGER DEFAULT 0,
                engagement_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
        """)
        await self._db.commit()

    async def close(self):
        if self._db:
            await self._db.close()

    # ── Sessions ──

    async def create_session(self, session_id: str, url: str, ai_mode: str) -> None:
        await self._db.execute(
            "INSERT INTO sessions (id, url, ai_mode, start_time, status) VALUES (?, ?, ?, ?, ?)",
            (session_id, url, ai_mode, datetime.utcnow().isoformat(), "active"),
        )
        await self._db.commit()

    async def update_session_teams(self, session_id: str, home: str, away: str) -> None:
        await self._db.execute(
            "UPDATE sessions SET home_team=?, away_team=? WHERE id=?",
            (home, away, session_id),
        )
        await self._db.commit()

    async def end_session(self, session_id: str, duration: float = 0) -> None:
        await self._db.execute(
            "UPDATE sessions SET end_time=?, duration=?, status='completed' WHERE id=?",
            (datetime.utcnow().isoformat(), duration, session_id),
        )
        await self._db.commit()

    async def get_sessions(self, limit: int = 50) -> List[Dict]:
        cursor = await self._db.execute(
            "SELECT * FROM sessions ORDER BY start_time DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_session(self, session_id: str) -> Optional[Dict]:
        cursor = await self._db.execute("SELECT * FROM sessions WHERE id=?", (session_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    # ── Events ──

    async def save_event(self, session_id: str, event_data: Dict[str, Any]) -> int:
        """Save a detected event. Returns the event ID."""
        confirmed = 1 if any(
            event_data.get(k) for k in ("confirmed_goal", "confirmed_card", "confirmed_penalty")
        ) else 0
        engagement = event_data.get("engagement")

        cursor = await self._db.execute(
            """INSERT INTO events
               (session_id, time_sec, frame_index, event_type, tension_score,
                description, sentiment, crowd_intensity, detections_json,
                confirmed, engagement_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                event_data.get("time_sec", 0),
                event_data.get("frame_index"),
                event_data.get("event_type", "normal_play"),
                event_data.get("tension_score", 0),
                event_data.get("description"),
                event_data.get("sentiment"),
                event_data.get("crowd_intensity", -1),
                json.dumps(event_data.get("detections", [])),
                confirmed,
                json.dumps(engagement) if engagement else None,
                datetime.utcnow().isoformat(),
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_events(self, session_id: str, event_type: Optional[str] = None) -> List[Dict]:
        if event_type:
            cursor = await self._db.execute(
                "SELECT * FROM events WHERE session_id=? AND event_type=? ORDER BY time_sec",
                (session_id, event_type),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM events WHERE session_id=? ORDER BY time_sec",
                (session_id,),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def export_events_csv(self, session_id: str) -> str:
        events = await self.get_events(session_id)
        output = io.StringIO()
        if events:
            writer = csv.DictWriter(output, fieldnames=events[0].keys())
            writer.writeheader()
            writer.writerows(events)
        return output.getvalue()
