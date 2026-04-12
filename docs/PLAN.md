# Execution Plan — Vio Vision Demo for Viaplay/TV2

This document captures the 3 sprints executed to build the Vio Vision
production-grade demo, plus the roadmap for Sprint 4 and beyond.

---

## Scope (decisions made up front)

Based on requirements elicitation with the project owner:

| Question | Answer |
|----------|--------|
| Who is the customer? | **Viaplay** (SE/DK/NO/FI) + **TV2** (DK/NO) |
| What do they do with the output? | **Broadcast overlay (<1s)** + **near-live data feed (2-5s)** |
| Signal formats they'll send | **All four**: SRT, RTMP, HLS, MP4 |
| Concurrent matches in production | Design for scale: start 1-2, grow to 10+ without rewrite |
| Overlay delivery mechanism | **Metadata feed only** (gRPC + WebSocket). They render graphics in their own systems (Vizrt/Chyron). |
| Timeline | Demo-ready in 2-4 weeks |

These decisions drove every technical choice below.

---

## Sprint 1 — Microservices foundation ✅

**Commit**: `74beb9b`

**Goal**: break the monolithic `backend/` into 3 decoupled services
communicating through Redis, and add multi-protocol ingest.

### What was built

| Service | Purpose | Key implementation |
|---------|---------|---------------------|
| `vio-ingestion/` | Video decoder | OpenCV+FFmpeg supporting SRT/RTMP/HLS/MP4. Auto-reconnect for live streams. Downsamples to 640x360, publishes JPEG bytes to Redis with 10s TTL + pub/sub announcements. |
| `vio-inference/` | ML pipeline (GPU) | YOLOv8m + ByteTrack tracker, 3-cluster team classification (home/away/ref), crowd filtering. Subscribes to Redis pattern `session:*:frames`. |
| `vio-gateway/` | Delivery layer | FastAPI WebSocket + REST. Converts new payload format to legacy so existing frontend works unchanged. |

### Infra additions

- `shared/proto/match_events.proto` — gRPC contract (all types, 3 RPCs).
- `docker-compose.yml` — local stack: redis + 3 services.
- `Makefile` — `make up`, `make dev-web`, `make build`, `make push`.
- `ARCHITECTURE.md` — service boundaries and data flow diagrams.

### Outcome

End of Sprint 1: `docker compose up` + `npm run dev` gives a working local
stack. Frontend still works via the gateway's backward-compat conversion.
Multi-protocol ingest verified with HLS and MP4 test streams.

---

## Sprint 2 — Intelligence + persistence + gRPC ✅

**Commit**: `8690c3a`

**Goal**: turn the bounding-box demo into something Viaplay/TV2 would pay
for. Add real football event detection, make enrichment cost-controlled,
persist everything, and open the B2B gRPC feed.

### Phase A — Postgres foundation

- Added **TimescaleDB** service to `docker-compose.yml`.
- New `vio-gateway/src/database.py` with asyncpg pool, retry loop for
  startup race, schema for `sessions` + `events` (hypertable on
  `created_at` when TimescaleDB is available).
- Persistence hook in gateway's Redis listener — fire-and-forget writes.
- REST endpoints:
  - `GET /api/sessions` (Postgres history)
  - `GET /api/sessions/{id}`
  - `GET /api/sessions/{id}/events?event_type=&start_sec=&end_sec=`
  - `GET /api/sessions/{id}/export?format=json|csv`
  - Renamed the previous `/api/sessions` → `/api/sessions/active`.

### Phase B — Event detector (the core value)

New `vio-inference/src/event_detector.py` — per-session state machine with:

- **Ball history** (deque of last 30 samples with x/y/t)
- **Possession rolling window** (deque of last 50 nearest-player teams)
- **Tension score** with 0.92/frame decay, capped at 10

Heuristics that emit events:

| Event | Trigger |
|-------|---------|
| `possession_change` | Nearest-player team switches (rolling majority) |
| `shot_on_goal` | Ball speed > 0.35 u/s heading into left/right 10% of frame |
| `out_of_bounds` | Ball position exits `[0, 1]` |
| `corner` | OOB recently + ball near a corner (5%) |
| `fast_break` | `possession_change` while tension > 5.5 |

Tension bumps: shot +3, corner +1, possession_change +1, fast_break +4,
OOB +0.5. All math in-memory, <1ms per frame.

### Phase C — Audio + GPT-4o enrichment

- Ported `backend/audio_analyzer.py` as-is — FFmpeg-based RMS crowd
  intensity per 1-second window, normalized 0-10.
- New `vio-inference/src/ai_enrichment.py` — `AIEnrichment.maybe_enrich()`:
  - Fires only when heuristic event in `{shot_on_goal, corner,
    possession_change, fast_break, out_of_bounds}` OR tension ≥ 7
  - Rate limited to 1 call / 3 sec / session
  - Kill switch: `AI_ENRICHMENT_DISABLED=1` env var
  - Async via `asyncio.to_thread` so the blocking Azure OpenAI SDK doesn't
    stall the inference loop
- Audio loaded in background task (`asyncio.to_thread`) so inference
  continues with `crowd=-1` until ready.

### Phase D — gRPC server

- Generated stubs from `shared/proto/match_events.proto` via `make proto`
  (with post-processing to fix the `import match_events_pb2` relative path).
- New `vio-gateway/src/grpc_server.py` implementing `MatchEventsServicer`:
  - `StreamEvents(session_id, filters)` — server-streaming live frames
    via Redis pub/sub subscribe
  - `ListSessions(limit, offset, status)` — from Postgres
  - `GetSessionEvents(session_id, start, end)` — historical stream
- JSON → protobuf converters for all payload shapes.
- Runs on port 50051 as `app.state.grpc_server` background task.

### Phase E — Frontend polish

- `LiveDataFeed.tsx` — scrollable raw-JSON panel (last 200 messages,
  pause/auto-scroll toggles). Demo wow factor for the B2B feed.
- `PresetUrlPicker.tsx` — chips (MP4 / HLS / Custom) replacing the raw
  URL input.
- `ConnectionStatus.tsx` — 3 dots (gateway/ingest/inference) polling
  every 5s.
- Integrated into `web/app/page.tsx` with `wsMessages` state.

### Outcome

End of Sprint 2: the same `docker compose up` now emits
`event_type: shot_on_goal` from pure heuristics within a few seconds of
starting a session. With Azure OpenAI credentials, GPT-4o confirms and
enriches significant events. Postgres persists everything. `grpcurl`
works end-to-end.

---

## Sprint 3 — Production readiness ✅

**Commits**: `9508a12`, `bbb83c6`

**Goal**: harden the stack enough to hand it over to Viaplay/TV2 engineers
and run a real load test against it on Azure.

### API key authentication

- New `vio-gateway/src/auth.py` — `VIO_API_KEYS` env var parser with
  `KEY:label` syntax. Disabled (dev mode) when env is unset.
- REST: `Depends(require_api_key)` on all historical endpoints. Accepts
  either `x-api-key` or `Authorization: Bearer <key>` headers.
- gRPC: per-RPC `_check_auth(context)` that aborts with UNAUTHENTICATED
  when metadata `x-api-key` is missing or unknown.
- Client label surfaces in logs for traceability.

### Prometheus metrics

`/metrics` endpoint added to all 3 services:

- **gateway**: `ws_connections`, `ws_messages_sent`, `grpc_requests`,
  `grpc_stream_frames`, `events_persisted`, `sessions_created`,
  `http_request_duration` (histogram)
- **inference**: `sessions_active`, `frames_processed`, `events_emitted`
  (by type), `ai_enrichments` (by trigger), `frame_latency` (histogram)
- **ingestion**: `sessions_active`, `frames_published` (by source_type),
  `errors`

### B2B client example

`examples/grpc-client/demo.py` — 150-line Python reference for Viaplay/TV2:

```bash
python demo.py list
python demo.py stream <session_id> --filter shot_on_goal --api-key $KEY
python demo.py history <session_id> --start 0 --end 300
```

This is what the broadcasters actually integrate into their graphics
pipelines. Because it's protobuf, a Go/Java/Node version is trivial.

### Load testing

`tools/loadtest.sh` — spawns N concurrent analysis sessions, polls
`/metrics` every 5s, prints sessions/events/ws throughput. Clean
tear-down on Ctrl-C.

### K8s manifests (production)

`k8s/microservices/` contains:

- `redis.yaml` — StatefulSet with 4GB maxmemory LRU
- `ingestion.yaml` — Deployment (2 replicas)
- `inference.yaml` — Deployment with GPU taint/toleration (commented
  for easy enable once GPU pool is ready)
- `gateway.yaml` — Deployment (2 replicas), exposes both 8000 REST and
  50051 gRPC, secretKeyRef for Postgres DSN + API keys

### Terraform (infrastructure as code)

`infra/terraform/main.tf` + `ai_services.tf`:

- Resource group in Sweden Central
- ACR Standard
- AKS with system pool (D4s_v5) + **GPU pool** (NC4as_T4_v3, autoscale 1-5)
- Redis Premium P1 (6GB, TLS only, allkeys-lru)
- PostgreSQL Flexible GP_D2ds_v5 with TimescaleDB extension enabled
- Azure Key Vault with tenant-scoped access + AKS kubelet access policy
- Azure OpenAI S0 + gpt-4o deployment (30K TPM)
- Azure AI Vision S1 (jersey OCR)
- Log Analytics + Application Insights
- Auto-generates B2B API keys and stores them in Key Vault

`post-apply.sh` — one-liner to sync all K8s secrets from Key Vault.

### README rewrite

Full architecture diagram, features table, quick start, Azure deploy
guide, REST + gRPC API reference, event types, config vars, directory
map, 4-sprint roadmap.

### Outcome

End of Sprint 3: `terraform apply` + `bash post-apply.sh` + `make ship`
provisions the whole stack on Azure in Sweden Central and hands Viaplay
a Python gRPC client they can run in 5 minutes.

---

## Sprint 4 — Roadmap (not yet executed)

What's needed for true production (not just demo):

### Networking & security

- **Custom domain + TLS** via nginx-ingress or Istio + cert-manager
- **Sticky sessions** for WebSocket via `session cookie` affinity
- **Private networking**: VNet with private endpoints for Redis +
  Postgres (+$150/mo for gateway + private DNS zones)
- **Network policies** to lock down pod-to-pod traffic

### Live stream ingestion

- **SRT listener** sidecar (`srt-live-server` or Haivision SRT Gateway)
  on a dedicated VM with public UDP port — required for actual broadcast
  contribution from Viaplay/TV2
- **RIST** alternative for redundant path ingestion (SMPTE 2022-7)

### ML pipeline enhancements

- **TrackNet v3** dedicated ball tracker (YOLO misses the ball ~30% of the
  time in real matches)
- **Homography** — keypoint detector + RANSAC to map pixel coords to
  pitch coords. Enables xG, pressing intensity, tactical heatmaps.
- **Action recognition** (MViT / VideoMAE) triggered on high-tension
  moments for nuanced event classification
- **Re-identification** with OSNet embeddings in Milvus for player IDs
  that persist across camera cuts

### Scaling & reliability

- **KEDA** autoscaler on the GPU pool based on Redis queue lag
- **Multi-region** failover: primary Sweden Central + DR in West Europe
- **Circuit breakers** on GPT-4o / AI Vision calls
- **Postgres HA**: high-availability flexible server + 35-day backup
  retention

### Delivery & CI/CD

- **GitHub Actions** pipeline: build + test + push on commit; auto-deploy
  on tag
- **Azure Container Apps** as an alternative deploy target for the
  gateway (cheaper than AKS for low-volume B2B endpoints)
- **OpenTelemetry** instrumentation (traces, not just metrics) so we can
  diagnose latency spikes across the 3-hop path

---

## What worked well

- **Decoupling early** — splitting the monolith in Sprint 1 made every
  subsequent addition (DB, auth, metrics, gRPC) land in exactly the
  right service.
- **Heuristics before AI** — the event detector produces value in <1ms
  per frame without calling any paid API. GPT-4o only runs on the
  moments that matter, keeping cost predictable.
- **Backward-compat converter** — the gateway's `_convert_detection_payload`
  let us refactor the backend without touching the frontend.
- **Infrastructure as code from day one** — Terraform + post-apply
  script means we can tear down and rebuild the stack in 15 minutes.

## What we'd do differently

- **Start with Postgres, not SQLite** — the monolith carried SQLite for
  longer than it should have; the migration to asyncpg was more work
  than starting with it.
- **Generate proto stubs in Docker build** — committing them is
  convenient for clients but drifts from the source if forgotten.
  A `make proto` pre-commit hook would help.
- **Instrument before shipping** — we added Prometheus metrics in
  Sprint 3. Earlier would have given us load-test data throughout the
  build instead of at the end.
