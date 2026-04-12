# Sprint 4 — Match Context Engine

## Why this matters

Sprints 1-3 built the detection + delivery pipeline. Every frame produces
bounding boxes, possession percentages, and events. But each GPT-4o call sees
**one frame in isolation** — no score, no accumulated stats, no narrative.

For Viaplay/TV2, that's the gap between "demo" and "product". They don't pay
for bounding boxes — they pay for **understanding**:

- "0-2 in minute 67, away team dominating with 65% possession"
- "Player #4 already has a yellow card — this second one would be a red"
- "3rd corner for home in the last 5 minutes, pressing intensifies"
- "Sentiment went calm → euphoric after the visiting team's second goal"

This sprint adds the layer that does the interpreting: **accumulated match
memory + temporal reasoning + narrative AI**.

## Scope

Six phases, ordered by impact. A, B, C give the biggest visible demo bump and
can ship first. D, E, F are follow-ons.

### Phase A — Match State core (`vio-inference/src/context_engine.py`)

New `MatchState` class per session that accumulates:

- **Team stats**: goals, shots, corners, fouls, cards (yellow/red), substitutions
- **Per-player stats**: keyed by `track_id`, cross-referenced with jersey_number
  when known. Tracks cards, fouls committed/received, shots, cumulative distance.
- **Match meta**: minute (if known from scoreboard OCR, else `None`), score,
  home/away team names.
- **Momentum**: rolling 5-minute window of possession + event tension, flagged
  as `home_dominant` | `away_dominant` | `balanced`.
- **Recent events queue**: last 20 events (more than AIEnrichment's current 5).

`MatchState.ingest(event, tracks, possession)` called from `_infer_frame`
after `event_detector.process()`. O(1) updates, no external calls.

New event type `player_milestone` emitted when:
- A player gets their 2nd yellow → warn "next offense = red"
- A team hits 5+ shots → "aggressive attack"
- A team hits 10+ fouls → "getting physical"

### Phase B — Context-aware prompting (`ai_enrichment.py` changes)

Modify `_build_prompt()` to inject a MATCH CONTEXT section:

```
MATCH CONTEXT (minute 67):
  Score: home 0 - away 2
  Shots: home 4 — away 11
  Fouls: home 12 — away 7
  Cards: home 1Y — away 2Y
  Momentum: away_dominant (last 5 min: 68% possession away, +3 shots)
  Player #4 (away) has 1 yellow card already.
  Recent events (chronological):
    63:12  shot_on_goal (away)  tension=7.0
    64:45  corner (away)        tension=5.2
    65:23  goal (away)           tension=9.0 CONFIRMED
    67:10  foul (home #5)        tension=4.5
```

GPT-4o with this context produces much richer descriptions and catches
narrative arcs ("this is the third foul by #5 in 10 minutes — referee
will likely show a card soon").

### Phase C — Periodic narrative engine

Every 2 minutes of match time (not wall-clock), call GPT-4o with:

- The current MatchState JSON
- One representative frame (latest frame for the session)
- Prompt: "Generate a 1-2 sentence broadcast-style narrative summary."

Output goes into a new event type `match_narrative` that lands in the sidebar.

```
67:00  📻 Match narrative
"Away team dominating the second half — leading 2-0 since minute 58.
 Home's pressing has created 3 corners but no clear shots in the last 5 min."
```

Rate limited separately from frame enrichment (own cooldown, own trigger).

### Phase D — Scoreboard OCR real-time (optional, follow-up)

New `vio-inference/src/scoreboard_ocr.py`:
- Crop top-right 25% of frame every 10 seconds
- Azure AI Vision Read API
- Regex parse `\d+[-:]\d+` for score, `\d{1,2}['′]` or `\d{1,2}:\d{2}` for minute
- Update `match_state.score` + `match_state.minute`

Falls back to "time since start" if OCR fails. Makes the match minute
accurate to what broadcast shows.

### Phase E — Commentator transcript (big follow-up, not in this sprint)

Azure Speech Services real-time streaming. Keyword detection ("gol",
"tarjeta", "falta") feeds strong event priors. Recent lines go into GPT-4o
prompt. Requires Azure Speech setup + audio streaming pipe.

Defer to Sprint 5.

### Phase F — Frontend Match Context panel

New `web/components/MatchContextPanel.tsx`:

- Score + minute header (big, center-top of panel or above pitch)
- Side-by-side team stats (home vs away): possession %, shots, corners,
  fouls, cards
- Momentum arrow → direction + intensity
- Latest narrative quote at the bottom
- Per-player card/foul tooltip when hovering a tracked player

`page.tsx` consumes new fields from the WebSocket payload:
`context: MatchState` added to every event.

`EventsSidebar` gets a new filter for "Narrative" events.

## Files

### New
- `vio-inference/src/context_engine.py` (~300 lines)
- `web/components/MatchContextPanel.tsx` (~150 lines)

### Modified
- `vio-inference/src/main.py` — wire context_engine into `_infer_frame`, add
  periodic narrative task
- `vio-inference/src/ai_enrichment.py` — inject match_state into prompt, add
  `generate_narrative()` method
- `vio-inference/src/event_detector.py` — emit `player_milestone` when state
  thresholds crossed (the emission itself stays in EventDetector, logic in
  context_engine)
- `vio-gateway/src/main.py` — pass through new event types, persist
  `match_narrative` and `player_milestone`
- `shared/proto/match_events.proto` — add `MatchState` message + `context`
  field on `MatchFrame`
- `web/types/events.ts` — add MatchState interface + new EventTypes
- `web/app/page.tsx` — track `matchState` state, render `MatchContextPanel`
- `web/components/EventsSidebar.tsx` — filter for Narrative

## Prompt engineering (the real work of Phase B/C)

The context section in the prompt is the make-or-break detail. Key choices:

- **Keep it short** — dense tables, not prose. GPT-4o tokens are expensive.
- **Only include non-zero stats** — skip `Cards: home 0Y — away 0Y` if both 0
- **Events in chronological order, not reverse** — GPT reads better forward
- **Highlight thresholds explicitly** — "Player #4 has 1 yellow card already"
  rather than leaving GPT to figure it out
- **Use the jersey number when available** — say "#10 Haaland" not "player 5"

## Verification

Each phase has an observable outcome:

- **A**: start a session, let it run 2 min, check `/api/sessions/{id}/events`
  — should see `player_milestone` events when thresholds cross.
- **B**: trigger a known event (e.g., paste a match clip with a foul), compare
  GPT-4o description with/without context — should reference match state.
- **C**: every 2 min should see a `match_narrative` event in the sidebar with
  a coherent summary.
- **D** (if included): manually verify the scoreboard OCR vs the video.
- **F**: visual — open the dashboard, confirm the score/minute/stats panel
  updates live.

## What this gives us competitive vs pros

After Sprint 4 we have rough parity with Sportradar/Opta in **narrative
capability** (which is what broadcasters pay for). We still lack:

- Multi-camera triangulation (TRACAB) — requires stadium hardware, skip
- Human-in-the-loop verification — can add later as an operator UI
- Fine-tuned football YOLO — separate sprint (training pipeline)

## Estimated effort

| Phase | Complexity | Value |
|-------|-----------|-------|
| A | Medium (~1 day) | High — foundation |
| B | Low (~2 hrs) | Very high — better AI responses |
| C | Low (~3 hrs) | Very high — narrative demo moment |
| D | Medium (~1 day, Azure dep) | Medium |
| E | High (~2 days, Speech setup) | High — but complex, defer |
| F | Medium (~4 hrs) | High — makes it visible |

Phases A + B + C + F = 2 days of focused work. That's what we ship this sprint.
