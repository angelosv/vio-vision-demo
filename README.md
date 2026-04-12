# Vio Vision

Real-time football video analytics platform. Ingests broadcast video over
**SRT / RTMP / HLS / MP4**, runs multi-model ML inference on GPU (YOLO +
ByteTrack + jersey OCR + event heuristics + GPT-4o enrichment), and delivers
live data feeds over **WebSocket** and **gRPC streaming** for broadcasters.

Built for Nordic broadcast partners (Viaplay, TV2). Azure-native.

## Architecture

```
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│  vio-ingestion   │   │  vio-inference   │   │  vio-gateway     │
│  :8001           │   │  :8002 (GPU)     │   │  :8000 + :50051  │
│                  │   │                  │   │                  │
│  • SRT/RTMP/HLS/ │──▶│  • YOLO+ByteTrack│──▶│  • WebSocket     │
│    MP4 decoder   │   │  • Jersey OCR    │   │  • gRPC stream   │
│  • Redis publish │   │  • Event detect  │   │  • REST + auth   │
│                  │   │  • GPT-4o enrich │   │  • Prometheus    │
└──────────────────┘   └──────────────────┘   └──────────────────┘
         │                      │                      │
         ▼                      ▼                      ▼
      ┌───────────────────────────────────────────────────┐
      │            Azure Cache for Redis Premium          │
      │  frames:{id}:{idx}          (JPEG bytes, TTL 10s) │
      │  session:{id}:frames        (pub/sub)             │
      │  session:{id}:detections    (pub/sub)             │
      └───────────────────────────────────────────────────┘
                                │
                                ▼
                    ┌─────────────────────────┐
                    │  Azure PostgreSQL +     │
                    │  TimescaleDB extension  │
                    │  (sessions, events)     │
                    └─────────────────────────┘

    Frontend (Next.js) ◀── WebSocket ── vio-gateway
    Viaplay / TV2      ◀── gRPC ──────── vio-gateway:50051
```

## Key features

| Feature | What |
|---------|------|
| **Multi-protocol ingest** | SRT (broadcast pro), RTMP, HLS, MP4 via OpenCV/FFmpeg |
| **Player tracking** | YOLOv8m + ByteTrack with persistent track IDs |
| **Team classification** | 3-cluster k-means on jersey color (home/away/referee) |
| **Jersey number OCR** | Azure AI Vision Read API + temporal voting |
| **Event detection** | Heuristics for possession, shot on goal, corner, OOB, fast break |
| **AI enrichment** | GPT-4o confirms events + generates broadcast-quality descriptions |
| **Crowd analysis** | Audio RMS intensity feeds AI prompt context |
| **B2B delivery** | gRPC streaming with API key auth, WebSocket for frontend |
| **Persistence** | Postgres + TimescaleDB hypertable for time-series queries |
| **Observability** | Prometheus metrics + Application Insights + structured logs |
| **Auto-scaling** | KEDA + HPA based on Redis queue lag (roadmap) |

## Quick start (local development)

Requirements: Docker, Node 18+, Python 3.11+.

```bash
# 1. Start the full stack
make up

# 2. In another terminal, start the frontend
make dev-web

# 3. Open http://localhost:3000
# 4. Pick a preset URL or paste your own, click Start Analysis
```

Test the gRPC feed (another terminal):

```bash
cd examples/grpc-client
pip install grpcio grpcio-tools
python -m grpc_tools.protoc -I=../../shared/proto \
  --python_out=. --grpc_python_out=. \
  ../../shared/proto/match_events.proto

# List past sessions
python demo.py list

# Stream a live session (grab session_id from /api/sessions/active)
python demo.py stream <session_id>

# Filter to specific event types
python demo.py stream <session_id> --filter shot_on_goal --filter corner
```

## Deploy to Azure

### 1. Provision infrastructure

```bash
cd infra/terraform
terraform init
terraform apply
```

Creates AKS (+ GPU pool), Redis Premium, Postgres Flexible, ACR, App Insights
in **Sweden Central** (closest to Viaplay/TV2). See `infra/terraform/README.md`.

### 2. Build + push images

```bash
make build  # build-ingestion + build-inference + build-gateway + build-web
make push   # push all to ACR
```

### 3. Deploy to AKS

```bash
# Get cluster credentials
az aks get-credentials -g viovision-demo-rg -n viovision-demo-aks

# Create secrets (see infra/terraform/README.md for commands)
# - reachuqa2          (ACR image pull)
# - vio-postgres       (DSN)
# - vio-api-keys       (B2B clients)
# - vio-openai-secret  (Azure OpenAI)
# - vio-ai-vision      (Azure AI Vision)

# Deploy
kubectl create namespace vio-demo
kubectl apply -f k8s/microservices/
```

### 4. Load test

```bash
bash tools/loadtest.sh 3 /path/to/sample.mp4
```

## API reference

### REST endpoints (vio-gateway)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET  | `/api` | public | Service info |
| GET  | `/metrics` | public | Prometheus exposition |
| POST | `/api/start` | public | Start analysis session |
| POST | `/api/stop[/{id}]` | public | Stop session(s) |
| GET  | `/api/sessions/active` | public | Active sessions (from ingestion) |
| GET  | `/api/sessions` | auth | Session history (Postgres) |
| GET  | `/api/sessions/{id}` | auth | Session detail |
| GET  | `/api/sessions/{id}/events` | auth | Filtered events |
| GET  | `/api/sessions/{id}/export` | auth | JSON or CSV |
| WS   | `/ws/live/{id}` | public | Live detection stream |

`auth` = requires `x-api-key` or `Authorization: Bearer <key>` header when
`VIO_API_KEYS` env var is set.

### gRPC service (`:50051`)

Defined in `shared/proto/match_events.proto`:

- `StreamEvents(session_id, [filter])` — server-streams `MatchFrame`
- `ListSessions(limit, offset, status)` — `ListSessionsResponse`
- `GetSessionEvents(session_id, start, end)` — server-streams `MatchFrame`

Requires `x-api-key` metadata header when auth is enabled.

### Event types

Heuristic (free, fast): `possession_change`, `shot_on_goal`, `out_of_bounds`, `corner`, `fast_break`

GPT-4o refinement (rate-limited, visual): `goal`, `goal_chance`, `celebration`,
`yellow_card`, `red_card`, `foul`, `penalty`, `offside`, `substitution`,
`corner`, `free_kick`, `normal_play`, `crowd_reaction`

## Configuration

Set via env vars (docker-compose or k8s secrets):

| Var | Service | Purpose |
|-----|---------|---------|
| `REDIS_URL` | all | Redis connection string |
| `POSTGRES_URL` | gateway | Postgres DSN |
| `INGESTION_URL` | gateway | Ingestion service URL |
| `AZURE_OPENAI_*` | inference | GPT-4o credentials |
| `AZURE_AI_VISION_*` | inference | Jersey OCR |
| `AI_ENRICHMENT_DISABLED` | inference | Kill switch for GPT-4o (cost) |
| `VIO_API_KEYS` | gateway | Comma-separated `key:label` |
| `GRPC_PORT` | gateway | gRPC server port (default 50051) |
| `YOLO_MODEL` | inference | Weights file (default yolov8m.pt) |
| `AI_INTERVAL_SEC` | inference | Min seconds between GPT-4o calls |

## Directory layout

```
vio-vision-demo/
├── vio-ingestion/          Multi-protocol decoder service
├── vio-inference/          ML pipeline service (GPU)
├── vio-gateway/            WebSocket + gRPC + REST service
├── shared/proto/           gRPC contract (match_events.proto)
├── web/                    Next.js dashboard (demo UI)
├── examples/grpc-client/   Sample B2B integration (Python)
├── tools/loadtest.sh       Multi-stream load test harness
├── infra/terraform/        Azure resource provisioning
├── k8s/microservices/      K8s manifests per service
├── docker-compose.yml      Local dev stack
├── Makefile                Build / push / deploy commands
└── ARCHITECTURE.md         Detailed architecture notes
```

## Roadmap

- **Sprint 1** — Microservices foundation, multi-protocol ingest, YOLO+ByteTrack
- **Sprint 2** — Event heuristics, GPT-4o enrichment, Postgres, gRPC server
- **Sprint 3** — Auth, metrics, load test, K8s, Terraform, docs
- **Sprint 4** (next) — Production hardening, SLA, multi-region, TrackNet ball, homography

## License

Proprietary.
