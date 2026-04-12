# Vio Vision — Microservices Architecture

## Overview

```
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│  vio-ingestion   │   │  vio-inference   │   │  vio-gateway     │
│  :8001           │   │  :8002 (GPU)     │   │  :8000           │
│                  │   │                  │   │                  │
│  • SRT/RTMP/HLS/ │─▶│  • YOLO+ByteTrack │─▶│  • WebSocket     │
│    MP4 decoder   │   │  • Jersey OCR    │   │  • gRPC (future) │
│  • Redis publish │   │  • Team cluster  │   │  • REST          │
└──────────────────┘   └──────────────────┘   └──────────────────┘
         │                      │                      │
         ▼                      ▼                      ▼
      ┌─────────────────────────────────────────────────┐
      │                  Azure Redis                    │
      │  frames:{id}:{idx}         (JPEG, TTL 10s)      │
      │  session:{id}:frames       (pub/sub)            │
      │  session:{id}:detections   (pub/sub)            │
      └─────────────────────────────────────────────────┘

    Frontend (Next.js) ─── WS ──▶ vio-gateway
                       ─── REST ▶ vio-gateway
```

## Services

### vio-ingestion (Python + OpenCV/FFmpeg)
**Port**: 8001

Accepts 4 input formats:
- **SRT** — broadcast professional contribution (`srt://host:port?streamid=...`)
- **RTMP** — legacy live streams (`rtmp://host:port/app/stream`)
- **HLS** — public playlists (`https://.../playlist.m3u8`)
- **MP4** — file URLs (Azure Blob, Firebase, etc.)

Decodes frames at 640x360, publishes JPEG bytes to Redis with 10s TTL, announces via pub/sub.

**Endpoints**:
- `POST /ingest {url, session_id?}` — start new session
- `POST /stop/{session_id}` — stop
- `GET /sessions` — list active
- `GET /health` — liveness

### vio-inference (Python + YOLO + CUDA)
**Port**: 8002 (no external traffic — internal only)

Subscribes to all `session:*:frames` channels. Per frame:
1. Read JPEG from Redis
2. YOLO + ByteTrack → persistent track IDs
3. 3-cluster team classification (home / away / referee)
4. Jersey OCR via Azure AI Vision (async, batched)
5. Publish detection payload to `session:{id}:detections`

Runs on GPU nodes (NCasT4_v3) in production. Falls back to CPU for dev.

### vio-gateway (Python + FastAPI)
**Port**: 8000 (public)

Fan-out service. WebSocket clients connect; the gateway reads from Redis pub/sub and relays to all connected clients for the session.

**Endpoints**:
- `POST /api/start {url}` — proxies to ingestion service
- `POST /api/stop` / `POST /api/stop/{session_id}`
- `GET /api/sessions` — active sessions
- `WS /ws/live/{session_id}` — modern per-session WebSocket
- `WS /ws` — legacy (accepts `{type: "join", session_id}` command)

Future: gRPC streaming for B2B clients (Viaplay/TV2).

## Data Flow

1. User/broadcaster calls `POST /api/start {url: "srt://..."}`
2. Gateway proxies to Ingestion, which returns `{session_id}`
3. Ingestion opens decoder, starts pushing frames to Redis
4. Inference workers pick up the new session via pub/sub pattern
5. Each frame: JPEG read → YOLO → detections published
6. Gateway relays detections to WebSocket clients
7. Frontend renders overlays on the video

## Local Development

```bash
# 1. Start the stack (requires Docker)
make up

# 2. In another terminal, start the frontend
make dev-web

# 3. Open http://localhost:3000
# 4. Paste an MP4/HLS URL and click Start
```

## Production (Azure)

```bash
# Build + push all images
make ship

# Deploy manifests
make deploy
```

Azure resources required:
- **AKS** with CPU pool (B4ms x2) + GPU pool (NC4as_T4_v3 x1-5 autoscale)
- **Azure Redis Premium** P1 (6GB, for frame cache)
- **Azure PostgreSQL Flexible** (event store, future)
- **Azure AI Vision** (Read API for jersey OCR)
- **Azure OpenAI** (GPT-4o for event analysis)
- **Azure Container Registry** (image hosting)
