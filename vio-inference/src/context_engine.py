"""Match Context Engine — accumulates per-match state and emits milestones.

Sits BETWEEN the frame-level event detector and the outbound publish step.
Every event from `event_detector.process()` is ingested here; the engine
maintains cumulative counters (per team and per tracked player) and recent
momentum, and emits its own *contextual* events when meaningful thresholds
cross.

This is the layer that turns "possession_change at frame 5723" into
"possession change — away team now at 68% for last 5 minutes, pressing
high". GPT-4o enrichment reads the resulting MatchState JSON so every
frame-level call knows the score, cards, fouls, and momentum.

Usage:
    engine = MatchContext(session_id)

    # Per frame, after EventDetector:
    extra_events = engine.ingest(events, tracks, possession, time_sec)
    snapshot = engine.snapshot()     # JSON-serializable state for AI prompts

    # Every 2 min of match time, trigger a narrative GPT-4o call:
    if engine.should_narrate(time_sec):
        # (caller passes snapshot + frame to AIEnrichment.generate_narrative)
        engine.mark_narrated(time_sec)
"""

from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Deque, Dict, List, Optional, Tuple


# ─── Configurable thresholds ────────────────────────────────────────────

MOMENTUM_WINDOW_SEC = 300.0        # 5-minute rolling window
NARRATIVE_INTERVAL_SEC = 120.0     # periodic narrative cadence (match time)
RECENT_EVENTS_CAP = 20             # fed into GPT-4o prompt
PLAYER_AGGRESSION_FOULS = 3        # player with this many fouls → milestone
TEAM_ATTACK_SHOTS = 5              # team with this many shots → milestone
TEAM_PHYSICAL_FOULS = 10           # team with this many fouls → milestone

# Which event types count as "shots"
SHOT_TYPES = {"shot_on_goal", "goal_chance", "goal"}


@dataclass
class TeamStats:
    goals: int = 0
    shots: int = 0
    corners: int = 0
    fouls: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    substitutions: int = 0


@dataclass
class PlayerStats:
    track_id: int
    team: str                       # "home" | "away" | "ref"
    jersey_number: str = ""         # best-guess, updated as OCR locks
    shots: int = 0
    fouls_committed: int = 0
    fouls_received: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    avg_position_x: float = 0.5     # running mean
    avg_position_y: float = 0.5
    _position_samples: int = 0


@dataclass
class RecentEvent:
    time_sec: float
    event_type: str
    team: Optional[str] = None
    description: Optional[str] = None
    tension_score: float = 0.0
    confirmed: bool = False


class MatchContext:
    """Per-session accumulated match state."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.home = TeamStats()
        self.away = TeamStats()
        self.players: Dict[int, PlayerStats] = {}

        # Score + minute — may be populated by scoreboard OCR (Phase D), else None
        self.scoreboard_minute: Optional[int] = None
        self.scoreboard_score: Optional[Tuple[int, int]] = None  # (home, away)
        self.home_team_name: Optional[str] = None
        self.away_team_name: Optional[str] = None

        # Rolling windows
        self._recent: Deque[RecentEvent] = deque(maxlen=RECENT_EVENTS_CAP)
        self._possession_history: Deque[Tuple[float, str]] = deque()  # (time_sec, team)

        # Narrative pacing
        self._last_narrative_time: float = -1e9

        # Player milestones already fired so we don't re-fire each frame
        self._player_milestones_fired: set[Tuple[int, str]] = set()
        self._team_milestones_fired: set[Tuple[str, str]] = set()

    # ─── Ingest per-frame events ─────────────────────────────────────────

    def ingest(
        self,
        events: List[Dict],
        tracks: List[Dict],
        possession: Optional[Dict],
        time_sec: float,
    ) -> List[Dict]:
        """Accumulate state, return any milestone events to emit this frame."""
        # Possession timeline for momentum calc
        if possession and possession.get("team") in ("home", "away"):
            self._possession_history.append((time_sec, possession["team"]))
            self._evict_old(time_sec)

        # Update / create player stats from tracks
        for t in tracks:
            tid = t.get("track_id")
            if tid is None:
                continue
            tid = int(tid)
            team = t.get("team", "")
            if tid not in self.players:
                obj_type = t.get("type", "")
                if obj_type == "Referee" or team == "ref":
                    continue  # don't accumulate stats for refs
                self.players[tid] = PlayerStats(track_id=tid, team=team)
            ps = self.players[tid]
            if team and not ps.team:
                ps.team = team
            jn = t.get("jersey_number") or ""
            if jn and jn != ps.jersey_number:
                ps.jersey_number = jn

            # Running-mean position
            pos = t.get("position", {})
            ps._position_samples += 1
            n = ps._position_samples
            ps.avg_position_x += (pos.get("x", 0.5) - ps.avg_position_x) / n
            ps.avg_position_y += (pos.get("y", 0.5) - ps.avg_position_y) / n

        # Ingest each event
        emitted: List[Dict] = []
        for ev in events:
            etype = ev.get("event_type", "")
            team = ev.get("team")
            desc = ev.get("description")
            tension = ev.get("tension_score", 0.0)
            confirmed = ev.get("confirmed", False)
            self._recent.append(RecentEvent(
                time_sec=time_sec, event_type=etype, team=team,
                description=desc, tension_score=tension, confirmed=confirmed,
            ))

            # Per-team counters
            ts = self._team(team)
            if ts is not None:
                if etype == "goal":
                    ts.goals += 1
                elif etype in SHOT_TYPES:
                    ts.shots += 1
                elif etype == "corner":
                    ts.corners += 1
                elif etype == "foul":
                    ts.fouls += 1
                elif etype == "yellow_card":
                    ts.yellow_cards += 1
                elif etype == "red_card":
                    ts.red_cards += 1
                elif etype == "substitution":
                    ts.substitutions += 1

            # Per-player counters (only when we have a clear actor)
            actor_tid = ev.get("track_id")  # future: set by event_detector if known
            if actor_tid is not None and actor_tid in self.players:
                ps = self.players[actor_tid]
                if etype == "foul":
                    ps.fouls_committed += 1
                elif etype == "yellow_card":
                    ps.yellow_cards += 1
                elif etype == "red_card":
                    ps.red_cards += 1
                elif etype in SHOT_TYPES:
                    ps.shots += 1

        # Emit milestones
        emitted.extend(self._check_player_milestones(time_sec))
        emitted.extend(self._check_team_milestones(time_sec))
        return emitted

    # ─── Snapshot for AI prompts ─────────────────────────────────────────

    def snapshot(self) -> Dict:
        """JSON-serializable match-state summary (used in GPT-4o prompts)."""
        return {
            "minute": self.scoreboard_minute,
            "score": (
                {"home": self.scoreboard_score[0], "away": self.scoreboard_score[1]}
                if self.scoreboard_score else None
            ),
            "teams": {
                "home": asdict(self.home),
                "away": asdict(self.away),
            },
            "momentum": self._compute_momentum(),
            "recent_events": [
                {
                    "time_sec": round(e.time_sec, 1),
                    "type": e.event_type,
                    "team": e.team,
                    "tension": e.tension_score,
                    "confirmed": e.confirmed,
                }
                for e in self._recent
            ],
            "players_flagged": [
                {
                    "track_id": ps.track_id,
                    "team": ps.team,
                    "jersey": ps.jersey_number,
                    "yellow_cards": ps.yellow_cards,
                    "fouls": ps.fouls_committed,
                }
                for ps in self.players.values()
                if ps.yellow_cards >= 1 or ps.fouls_committed >= PLAYER_AGGRESSION_FOULS
            ],
        }

    def compact_text(self) -> str:
        """Human-readable compact summary — fits in a GPT-4o prompt section."""
        lines: List[str] = []
        if self.scoreboard_minute is not None:
            lines.append(f"Match minute: {self.scoreboard_minute}'")
        if self.scoreboard_score:
            lines.append(f"Score: home {self.scoreboard_score[0]} - "
                         f"away {self.scoreboard_score[1]}")

        def team_line(label: str, ts: TeamStats) -> Optional[str]:
            parts = []
            if ts.goals: parts.append(f"goals={ts.goals}")
            if ts.shots: parts.append(f"shots={ts.shots}")
            if ts.corners: parts.append(f"corners={ts.corners}")
            if ts.fouls: parts.append(f"fouls={ts.fouls}")
            if ts.yellow_cards: parts.append(f"Y={ts.yellow_cards}")
            if ts.red_cards: parts.append(f"R={ts.red_cards}")
            if ts.substitutions: parts.append(f"subs={ts.substitutions}")
            return f"{label}: {', '.join(parts)}" if parts else None

        if (l := team_line("home", self.home)) is not None:
            lines.append(l)
        if (l := team_line("away", self.away)) is not None:
            lines.append(l)

        momentum = self._compute_momentum()
        if momentum["direction"] != "balanced":
            lines.append(
                f"Momentum: {momentum['direction']} "
                f"({momentum['home_percent']:.0f}% home, "
                f"{momentum['away_percent']:.0f}% away over last "
                f"{int(momentum['window_sec'])}s)"
            )

        # Flagged players
        flagged = [
            ps for ps in self.players.values()
            if ps.yellow_cards >= 1 or ps.fouls_committed >= PLAYER_AGGRESSION_FOULS
        ]
        for ps in flagged:
            label = f"#{ps.jersey_number}" if ps.jersey_number else f"track_id={ps.track_id}"
            lines.append(
                f"Player {label} ({ps.team}): "
                f"yellows={ps.yellow_cards}, fouls={ps.fouls_committed}"
                + (" [NEXT = RED]" if ps.yellow_cards >= 1 else "")
            )

        # Recent events in chronological order
        if self._recent:
            lines.append("Recent events (chronological):")
            for e in list(self._recent)[-6:]:  # last 6 for brevity
                team = f"({e.team})" if e.team else ""
                conf = " ✓" if e.confirmed else ""
                lines.append(
                    f"  {self._fmt_time(e.time_sec)}  {e.event_type} {team}"
                    f"  tension={e.tension_score:.1f}{conf}"
                )

        return "\n".join(lines) if lines else "(no state accumulated yet)"

    # ─── Narrative pacing ────────────────────────────────────────────────

    def should_narrate(self, time_sec: float) -> bool:
        """True if it's time for a periodic narrative summary."""
        return time_sec - self._last_narrative_time >= NARRATIVE_INTERVAL_SEC

    def mark_narrated(self, time_sec: float) -> None:
        self._last_narrative_time = time_sec

    # ─── Scoreboard updates (from future OCR module) ─────────────────────

    def update_scoreboard(
        self, minute: Optional[int] = None,
        home_score: Optional[int] = None, away_score: Optional[int] = None,
    ) -> None:
        if minute is not None:
            self.scoreboard_minute = minute
        if home_score is not None and away_score is not None:
            self.scoreboard_score = (home_score, away_score)

    def set_team_names(self, home: str, away: str) -> None:
        self.home_team_name = home
        self.away_team_name = away

    # ─── Internals ───────────────────────────────────────────────────────

    def _team(self, label: Optional[str]) -> Optional[TeamStats]:
        if label == "home":
            return self.home
        if label == "away":
            return self.away
        return None

    def _evict_old(self, now: float) -> None:
        while self._possession_history and now - self._possession_history[0][0] > MOMENTUM_WINDOW_SEC:
            self._possession_history.popleft()

    def _compute_momentum(self) -> Dict:
        if not self._possession_history:
            return {"direction": "balanced", "home_percent": 50.0,
                    "away_percent": 50.0, "window_sec": 0}
        total = len(self._possession_history)
        home = sum(1 for _, t in self._possession_history if t == "home")
        away = total - home
        home_pct = 100.0 * home / total
        away_pct = 100.0 * away / total
        if home_pct >= 62:
            direction = "home_dominant"
        elif away_pct >= 62:
            direction = "away_dominant"
        else:
            direction = "balanced"
        window = self._possession_history[-1][0] - self._possession_history[0][0]
        return {
            "direction": direction,
            "home_percent": round(home_pct, 1),
            "away_percent": round(away_pct, 1),
            "window_sec": round(window, 1),
        }

    def _check_player_milestones(self, time_sec: float) -> List[Dict]:
        emitted: List[Dict] = []
        for ps in self.players.values():
            # Second-yellow warning
            if ps.yellow_cards >= 1:
                key = (ps.track_id, "one_yellow")
                if key not in self._player_milestones_fired:
                    self._player_milestones_fired.add(key)
                    label = f"#{ps.jersey_number}" if ps.jersey_number else f"track_id={ps.track_id}"
                    emitted.append({
                        "event_type": "player_milestone",
                        "time_sec": time_sec,
                        "team": ps.team,
                        "description": f"Player {label} ({ps.team}) has 1 yellow — "
                                       f"next offense risks red card",
                        "tension_score": 5.0,
                    })
            # Aggression flag
            if ps.fouls_committed >= PLAYER_AGGRESSION_FOULS:
                key = (ps.track_id, "aggressive")
                if key not in self._player_milestones_fired:
                    self._player_milestones_fired.add(key)
                    label = f"#{ps.jersey_number}" if ps.jersey_number else f"track_id={ps.track_id}"
                    emitted.append({
                        "event_type": "player_milestone",
                        "time_sec": time_sec,
                        "team": ps.team,
                        "description": f"Player {label} ({ps.team}) committed "
                                       f"{ps.fouls_committed} fouls — getting physical",
                        "tension_score": 4.0,
                    })
        return emitted

    def _check_team_milestones(self, time_sec: float) -> List[Dict]:
        emitted: List[Dict] = []
        for name, ts in (("home", self.home), ("away", self.away)):
            if ts.shots >= TEAM_ATTACK_SHOTS:
                key = (name, "attacking")
                if key not in self._team_milestones_fired:
                    self._team_milestones_fired.add(key)
                    emitted.append({
                        "event_type": "player_milestone",   # reuse category
                        "time_sec": time_sec,
                        "team": name,
                        "description": f"{name} team has registered {ts.shots} shots — "
                                       f"attacking hard",
                        "tension_score": 5.5,
                    })
            if ts.fouls >= TEAM_PHYSICAL_FOULS:
                key = (name, "physical")
                if key not in self._team_milestones_fired:
                    self._team_milestones_fired.add(key)
                    emitted.append({
                        "event_type": "player_milestone",
                        "time_sec": time_sec,
                        "team": name,
                        "description": f"{name} team on {ts.fouls} fouls — physical match",
                        "tension_score": 4.5,
                    })
        return emitted

    @staticmethod
    def _fmt_time(sec: float) -> str:
        m = int(sec // 60)
        s = int(sec % 60)
        return f"{m:02d}:{s:02d}"
