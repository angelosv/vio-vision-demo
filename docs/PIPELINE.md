# Vio Vision Pipeline — 4 Layers from Frame to Feed

This document traces how a single video frame becomes a structured event
delivered to Viaplay/TV2. The pipeline is split into 4 conceptual layers,
each with a clear single responsibility.

## Layer map

```
┌──────────────┐   ┌──────────────┐   ┌───────────────┐   ┌──────────────┐
│ 1. DETECTAR  │→  │ 2. ANALIZAR  │→  │ 3. INTERPRETAR│→  │ 4. DISPARAR  │
│ (qué hay)    │   │ (quién es)   │   │ (qué pasa)    │   │ (emitir)     │
└──────────────┘   └──────────────┘   └───────────────┘   └──────────────┘
  YoloTracker      classify_teams      EventDetector +      Redis pub/sub
  (YOLO+ByteTrack) PlayerTracker       AIEnrichment +       → gateway →
                   JerseyOcr           MatchContext         WS + gRPC +
                                                             Postgres
```

| Layer | Input | Output | File(s) |
|-------|-------|--------|---------|
| **1. Detect** | JPEG frame | Raw detections (boxes + confidences) | `vio-inference/src/yolo_tracker.py:37` |
| **2. Analyze** | Raw detections + frame | `TrackedObject[]` with team, jersey | `yolo_tracker.py:86` + `jersey_ocr.py:77` |
| **3. Interpret** | Tracks + ball + audio + history | Event + context snapshot | `event_detector.py:67` + `ai_enrichment.py:75` + `context_engine.py` |
| **4. Emit** | Payload | WebSocket, gRPC, Postgres | `vio-gateway/src/main.py:90` + `grpc_server.py` |

---

## Layer 1 — Detectar

**File**: `vio-inference/src/yolo_tracker.py`

Receives a decoded 640×360 BGR frame, runs YOLOv8m + ByteTrack tracker, and
returns raw detections. Crowd filter (small boxes in upper 1/3 of frame)
drops tribune detections.

**Output shape** (raw):
```python
[
  {"track_id": 5, "label": "Player", "confidence": 0.92, "box": [x1,y1,x2,y2]},
  {"track_id": 31, "label": "Ball", "confidence": 0.79, "box": [...]},
]
```

**What this layer doesn't know**: who the player plays for, what number
they wear, whether this is normal play or a goal.

---

## Layer 2 — Analizar

Adds **identity** and **stability** to the raw detections.

**Team classification** (`yolo_tracker.py::classify_teams`, line 86): crops
each player's torso, runs 3-cluster k-means on HSV color. The smallest
cluster = referees. The other two = home / away. Each detection gains
`team` and `team_color`.

**Persistent tracking**: ByteTrack (inside Ultralytics) maintains `track_id`
across frames, surviving brief occlusions.

**Jersey OCR** (`jersey_ocr.py`): crops back region of each player every 2s,
sends to Azure AI Vision Read API, applies temporal voting over the last 5
reads, locks the jersey number when 3 consistent readings arrive.

**Output**:
```python
TrackedObject {
  track_id: 5, type: "Player", team: "home",
  jersey_number: "10", position: {x, y}, bbox, team_color
}
```

Now the system knows **who** each body is — but not yet **what is happening**.

---

## Layer 3 — Interpretar

This is the most important layer for broadcast value. Three sub-systems
running in parallel:

### 3a. Heuristics (`event_detector.py:67`)

Deterministic state machine. Per session maintains:
- `ball_history` (30 samples): for velocity-based shot detection
- `possession_window` (50 frames): rolling nearest-player team majority
- `tension_score`: decays 0.92/frame, bumped +3 on shots, +2 on fouls, etc.
- `slow_frames`: counter for stoppage detection

Emits events:
- `possession_change` — majority flip
- `shot_on_goal` — ball speed > 0.35 heading into goal area (10% width)
- `out_of_bounds` — ball exits [0,1]
- `corner` — OOB in defensive third followed by ball near corner
- `fast_break` — rapid possession change with elevated tension
- `stoppage` — ball under 0.02 u/s for 8+ frames
- `foul` — stoppage + referee near ball + 2+ players clustered

Runs in <1ms per frame. Zero external calls.

### 3b. GPT-4o enrichment (`ai_enrichment.py:75`)

Only fires when heuristics flag a trigger event OR tension ≥ 5.5.
Rate-limited to 1 call per 3 seconds per session. Kill switch:
`AI_ENRICHMENT_DISABLED=1` env var.

Receives: current frame + heuristic signal + **match context text**
(injected from Layer 3c).

Returns refined event_type, broadcast-quality description, sentiment,
confirmed flag.

### 3c. Match Context (`context_engine.py`) — NEW in Sprint 4

Per-session accumulator that gives the system **memory**. After each
frame's events pass through the detector, they're ingested here:

- **Team counters**: goals, shots, corners, fouls, yellow/red cards, subs
- **Per-player stats**: cards, fouls committed, shots, average position
- **Rolling momentum**: 5-minute window of possession → `home_dominant` |
  `away_dominant` | `balanced`
- **Flagged players**: who's carrying a yellow card, who's committed many fouls

Emits its own milestone events:
- `player_milestone` — "Player #4 has 1 yellow — next offense risks red"
- `player_milestone` (team) — "Home team registered 5 shots — attacking hard"

Every 2 minutes of match time, triggers a **narrative GPT-4o call**
(different prompt, broadcast-commentator voice):
```
"Away team dominating the second half — leading 2-0 since minute 58. Home's
pressing has created 3 corners but no clear shots in the last 5 min."
```

Emitted as `match_narrative` event.

### Critical wiring

In `_infer_frame`:

```python
events = session.detector.process(tracks, ball, frame_idx, time_sec)   # 3a
match_state_text = session.context.compact_text()                       # 3c
enriched = await session.enrichment.maybe_enrich(                       # 3b
    frame, events[0], tension, crowd, time_sec, possession,
    match_context=match_state_text,          # ← 3c feeds 3b
)
session.context.ingest(events + [refined_event], tracks, possession)   # 3c absorbs
if session.context.should_narrate(time_sec):                           # 3c narrative
    narrative = await session.enrichment.generate_narrative(frame, match_state_text)
```

---

## Layer 4 — Disparar

Once the payload is assembled, it fans out:

### 4a. Redis pub/sub
`vio-inference/src/main.py` publishes to `session:{id}:detections`. Separate
payloads are emitted for the main event, any milestone events, and narrative
events — each shows up as its own row in the sidebar.

### 4b. Gateway fan-out (`vio-gateway/src/main.py::ConnectionManager._listen`)
Gateway is pattern-subscribed to Redis. On each message:
1. **Persist** to Postgres (if event_type != normal_play OR tension ≥ 3)
2. **Broadcast** to WebSocket clients (frontend)
3. **Forward** to gRPC streaming subscribers (B2B clients)

### 4c. Frontend (`web/app/page.tsx`)
Sets state:
- `tracks`, `ball` → VideoPanel renders boxes
- `possession`, `context.teams` → BottomPanels + MatchContextPanel
- `event` (if non-normal) → appended to events sidebar

### 4d. gRPC (`vio-gateway/src/grpc_server.py`)
Dedicated channel for B2B. Same payload encoded as protobuf `MatchFrame`.
API key auth via `x-api-key` metadata header.

---

## Failure matrix

| If this layer fails... | Impact | Degradation |
|------------------------|--------|-------------|
| YOLO | No tracks | All events stop — pipeline frozen |
| Jersey OCR | No numbers | Tracks show `T-5` instead of `#10` |
| Team classifier | Wrong team | Home/away swapped |
| Heuristics (`event_detector`) | Bug in logic | Only GPT-4o fires events |
| GPT-4o | API down / rate limit | Only heuristic events + no narrative |
| Match Context | Bug | State reverts to per-frame (still works) |
| Audio (ffmpeg) | Not installed | `crowd_intensity = -1`, no audio context |
| Postgres | Connection down | Events stream live but aren't persisted |
| Redis | Down | Entire pipeline stops (it's the backbone) |
| gRPC server | Port collision | WS clients still work, B2B offline |
| WebSocket | Network | Frontend disconnects, auto-reconnects every 3s |

---

## Trace — 90-second yellow card scenario

Minute 23:04 of a match. Home player #10 advances with ball. Away defender #4
fouls him. Referee shows a yellow card.

FPS = 25, FRAME_INTERVAL = 10 in ingestion → inference processes ~2.5 fps.

### Frame at t=23:04.00 — normal play
- **L1**: YOLO returns 22 detections (20 players + 2 refs + 1 ball)
- **L2**: 3-cluster → team assignments; Azure AI Vision OCR reads `#10` for
  track_id=5 (already cached)
- **L3 heuristics**: ball barely moving, no event emitted
- **L3 context**: possession window still "home"
- **L4**: tracks + empty event published — sidebar empty

### Frame at t=23:04.80 — defender fouls #10
- **L1**: bounding boxes updated; #10 bbox noticeably lower (on ground)
- **L2**: tracks persist via ByteTrack
- **L3 heuristics**:
  - ball speed drops to 0.015 → `slow_frames = 2`
  - nearest-player switched to #4 → `possession_change` emitted
  - tension_score += 1 → 1.0
- **L3 AI**: heuristic triggered enrichment. GPT-4o sees frame + context text
  ("minute unknown, home shots=0, away shots=0, no recent events"). Returns
  `foul` with description "Player #10 brought down hard…", sentiment `tense`
- **L3 context**: ingests `possession_change` + re-ingests AI-refined `foul`.
  Player #4 fouls_committed → 1. Away team fouls → 1.
- **L4**: 2 payloads published (possession_change, foul).
  Gateway persists foul, broadcasts both.
  Frontend sidebar shows:
  - `23:04 Foul` (orange badge) with description
  - `23:04 Possession change` (emerald badge)

### Frame at t=23:05.60 — referee shows yellow
- **L1**: all players + ref still detected
- **L2**: unchanged
- **L3 heuristics**:
  - `slow_frames = 8` → threshold hit → `stoppage` emitted
  - referee near ball + 2+ players clustered → `foul` (heuristic re-fire)
  - tension_score += 2 → 3.0
- **L3 AI**: heuristic-flagged `foul` + tension 3.0 triggered enrichment.
  GPT-4o sees frame with MATCH CONTEXT section that now reads:
  ```
  home: fouls=1
  away: fouls=1
  Player T-4 (away): yellows=0, fouls=1
  Recent events:
    23:04  possession_change (away)  tension=1.0
    23:04  foul (away)  tension=1.0
  ```
  GPT-4o sees the referee holding the card up and returns:
  `yellow_card`, confirmed=true
- **L3 context**: ingests stoppage + AI-refined yellow_card.
  Player #4 yellow_cards → 1. Away team yellow_cards → 1.
  Because yellow_cards ≥ 1, `player_milestone` emitted:
  "Player #4 (away) has 1 yellow — next offense risks red card"
- **L4**: 3 payloads published (stoppage, yellow_card, player_milestone).
  Gateway persists all. Sidebar updates:
  - `23:05 Yellow card` CONFIRMED (yellow badge)
  - `23:05 Stoppage` (slate badge)
  - `23:05 Milestone` (amber badge) "Player #4 has 1 yellow…"
- **gRPC clients** (Viaplay / TV2) receive the same events as MatchFrame
  protobuf messages in their stream.

### Frame at t=25:00 — narrative check
Match context decides it's time for periodic narrative (2 min since last).
GPT-4o receives the full state JSON + frame:
```
"Tense first 25 minutes with both teams playing physical — away team leads
fouls count but home has seen only one card so far. Possession balanced,
scoreless."
```
Emitted as `match_narrative` event → sidebar shows purple "Narrative" card.

---

## Tuning knobs

Environment variables + constants ops can adjust:

| Knob | Default | Location | Effect |
|------|---------|----------|--------|
| `FRAME_INTERVAL` | 10 | ingestion config | Frames-per-inference ratio |
| `SHOT_SPEED_THRESHOLD` | 0.35 | `event_detector.py:34` | Ball velocity to count as shot |
| `STOPPAGE_SPEED_THRESHOLD` | 0.02 | `event_detector.py:42` | Below this = stopped |
| `STOPPAGE_FRAMES` | 8 | `event_detector.py:43` | Consecutive slow frames = stoppage |
| `FOUL_REF_DISTANCE` | 0.15 | `event_detector.py:46` | Ref-to-ball proximity for foul |
| `AI_INTERVAL_SEC` / `RATE_LIMIT_SEC` | 3.0 | `ai_enrichment.py:41` | Min gap between GPT-4o calls |
| `TENSION_TRIGGER` | 5.5 | `ai_enrichment.py:48` | Tension floor to fire enrichment |
| `NARRATIVE_INTERVAL_SEC` | 120.0 | `context_engine.py:17` | Periodic narrative cadence |
| `MOMENTUM_WINDOW_SEC` | 300.0 | `context_engine.py:16` | Rolling possession window |
| `TEAM_ATTACK_SHOTS` | 5 | `context_engine.py:21` | Shots threshold for team milestone |
| `PLAYER_AGGRESSION_FOULS` | 3 | `context_engine.py:20` | Fouls threshold for player milestone |
| `JERSEY_OCR_INTERVAL` | 2.0 | `jersey_ocr.py:33` | Seconds between OCR re-reads |
| `AI_ENRICHMENT_DISABLED` | (unset) | env var | Kill switch for all GPT-4o |
