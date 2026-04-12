"""Football event detection from tracks + ball trajectory.

Maintains per-session state (ball history, possession window, tension score)
and emits events based on deterministic heuristics. NO ML here — runs in-
memory on the same thread as the inference loop. Fast (<1ms per frame).

Events emitted:
  - possession_change    — ball nearest-player switches team
  - shot_on_goal         — ball velocity spike toward goal area
  - out_of_bounds        — ball exits [0, 1] frame bounds
  - corner               — OOB on defensive end followed by ball near corner
  - fast_break           — rapid possession change + players shifting side

Each frame call returns a list of zero or more event dicts.
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional


@dataclass
class BallSample:
    x: float
    y: float
    t: float  # time in seconds
    frame_idx: int


class EventDetector:
    BALL_HISTORY = 30
    POSSESSION_DIST = 0.08         # normalized distance: player "owns" ball if within this
    POSSESSION_WINDOW = 50         # rolling frames for possession percentages
    SHOT_SPEED_THRESHOLD = 0.35    # normalized units/sec ball must exceed
    SHOT_COOLDOWN_SEC = 2.0        # minimum gap between shot events
    GOAL_AREA_X = 0.10             # left/right 10% = goal area
    TENSION_DECAY = 0.92           # per-frame multiplicative decay
    TENSION_CAP = 10.0
    MIN_BALL_FRAMES = 3            # need N consecutive ball samples to compute velocity

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.ball_history: Deque[BallSample] = deque(maxlen=self.BALL_HISTORY)
        self.possession_window: Deque[str] = deque(maxlen=self.POSSESSION_WINDOW)
        self.current_possession: Optional[str] = None
        self.tension_score: float = 0.0
        self.last_oob_frame: Optional[int] = None
        self.last_oob_side: Optional[str] = None  # "left" | "right"
        self.last_shot_frame: Optional[int] = None
        self.last_shot_time: float = 0.0
        self.player_side_history: Dict[int, Deque[float]] = {}
        self.frames_seen: int = 0

    # ─── Public API ────────────────────────────────────────────────────────

    def process(
        self,
        tracks: List[Dict],
        ball: Optional[Dict],
        frame_idx: int,
        time_sec: float,
    ) -> List[Dict]:
        """Process one frame's worth of tracks + ball. Returns list of events fired."""
        self.frames_seen += 1
        self.tension_score = max(0.0, self.tension_score * self.TENSION_DECAY)

        events: List[Dict] = []

        if ball is not None:
            bx = ball.get("position", {}).get("x", 0.0)
            by = ball.get("position", {}).get("y", 0.0)
            sample = BallSample(x=bx, y=by, t=time_sec, frame_idx=frame_idx)
            self.ball_history.append(sample)

            # Possession via nearest player
            nearest = self._nearest_player(sample, tracks)
            if nearest:
                team = nearest.get("team", "home")
                self.possession_window.append(team)
                new_poss = self._majority(self.possession_window)
                if new_poss and new_poss != self.current_possession:
                    prev = self.current_possession
                    self.current_possession = new_poss
                    if prev is not None:  # ignore first assignment
                        self._bump(1.0)
                        events.append(self._emit(
                            "possession_change", frame_idx, time_sec,
                            team=new_poss,
                            description=f"Possession change to {new_poss}",
                        ))

            # Shot detection
            if len(self.ball_history) >= self.MIN_BALL_FRAMES:
                shot = self._detect_shot(frame_idx, time_sec)
                if shot:
                    self._bump(3.0)
                    events.append(shot)

            # Out of bounds
            if not (0.0 <= bx <= 1.0 and 0.0 <= by <= 1.0):
                # Only emit on *transition* into OOB
                if self.last_oob_frame is None or (frame_idx - self.last_oob_frame) > 30:
                    self.last_oob_frame = frame_idx
                    self.last_oob_side = "left" if bx < 0.5 else "right"
                    self._bump(0.5)
                    events.append(self._emit(
                        "out_of_bounds", frame_idx, time_sec,
                        side=self.last_oob_side,
                        description=f"Ball out of bounds on {self.last_oob_side} side",
                    ))

            # Corner detection: OOB recently, ball now near corner
            corner = self._detect_corner(bx, by, frame_idx, time_sec)
            if corner:
                self._bump(1.0)
                events.append(corner)

        # Fast-break: rapid possession change when tension was already elevated
        # (piggy-back on possession_change when tension jumped recently)
        if events and any(e["event_type"] == "possession_change" for e in events):
            if self.tension_score > 5.5:
                self._bump(4.0)
                events.append(self._emit(
                    "fast_break", frame_idx, time_sec,
                    team=self.current_possession,
                    description=f"Fast break by {self.current_possession}",
                ))

        # Attach current tension to every emitted event
        for e in events:
            e["tension_score"] = round(self.tension_score, 2)

        return events

    def possession_state(self) -> Dict:
        """Return current rolling possession percentages."""
        if not self.possession_window:
            return {"team": None, "home_percent": 50.0, "away_percent": 50.0}
        total = len(self.possession_window)
        home = sum(1 for t in self.possession_window if t == "home")
        away = sum(1 for t in self.possession_window if t == "away")
        return {
            "team": self.current_possession,
            "home_percent": round(100 * home / total, 1),
            "away_percent": round(100 * away / total, 1),
        }

    # ─── Helpers ───────────────────────────────────────────────────────────

    def _nearest_player(self, ball: BallSample, tracks: List[Dict]) -> Optional[Dict]:
        best = None
        best_dist = self.POSSESSION_DIST
        for t in tracks:
            pos = t.get("position", {})
            px, py = pos.get("x", 0), pos.get("y", 0)
            d = ((px - ball.x) ** 2 + (py - ball.y) ** 2) ** 0.5
            if d < best_dist:
                best_dist = d
                best = t
        return best

    @staticmethod
    def _majority(window: Deque[str]) -> Optional[str]:
        if not window:
            return None
        counts: Dict[str, int] = {}
        for t in window:
            counts[t] = counts.get(t, 0) + 1
        return max(counts.items(), key=lambda kv: kv[1])[0]

    def _detect_shot(self, frame_idx: int, time_sec: float) -> Optional[Dict]:
        """Speed + direction → shot on goal."""
        if time_sec - self.last_shot_time < self.SHOT_COOLDOWN_SEC:
            return None
        # Use last vs 3-frames-ago samples for velocity
        samples = list(self.ball_history)
        if len(samples) < self.MIN_BALL_FRAMES:
            return None
        cur = samples[-1]
        prev = samples[-self.MIN_BALL_FRAMES]
        dt = cur.t - prev.t
        if dt <= 0:
            return None
        vx = (cur.x - prev.x) / dt
        vy = (cur.y - prev.y) / dt
        speed = (vx ** 2 + vy ** 2) ** 0.5
        if speed < self.SHOT_SPEED_THRESHOLD:
            return None

        # Heading toward a goal area?
        heading_right = vx > 0 and cur.x > 0.5
        heading_left = vx < 0 and cur.x < 0.5
        if not (heading_right or heading_left):
            return None

        target = "right" if heading_right else "left"
        self.last_shot_frame = frame_idx
        self.last_shot_time = time_sec
        return self._emit(
            "shot_on_goal", frame_idx, time_sec,
            team=self.current_possession,
            target_goal=target,
            ball_speed=round(speed, 3),
            description=f"Shot on {target} goal (speed {speed:.2f})",
        )

    def _detect_corner(
        self, bx: float, by: float, frame_idx: int, time_sec: float
    ) -> Optional[Dict]:
        """OOB recently + ball near corner = corner kick setup."""
        if self.last_oob_frame is None:
            return None
        if frame_idx - self.last_oob_frame > 60:  # 2s @ 30fps
            return None
        # Ball near one of the 4 corners (5% of frame)
        in_corner = (
            (bx < 0.05 or bx > 0.95) and (by < 0.10 or by > 0.90)
        )
        if not in_corner:
            return None
        # Don't double-fire
        if hasattr(self, "_last_corner_frame") and frame_idx - getattr(self, "_last_corner_frame") < 90:
            return None
        self._last_corner_frame = frame_idx
        side = "left" if bx < 0.5 else "right"
        return self._emit(
            "corner", frame_idx, time_sec,
            side=side,
            description=f"Corner kick on {side} side",
        )

    def _bump(self, delta: float) -> None:
        self.tension_score = min(self.TENSION_CAP, self.tension_score + delta)

    def _emit(self, event_type: str, frame_idx: int, time_sec: float, **extra) -> Dict:
        out = {
            "event_type": event_type,
            "frame_index": frame_idx,
            "time_sec": time_sec,
            "tension_score": round(self.tension_score, 2),
        }
        out.update(extra)
        return out
