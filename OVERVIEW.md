# Vio Vision — Project Overview

Real-time football video analytics platform for Nordic broadcast partners
(Viaplay, TV2). Delivers live player/ball tracking, jersey number OCR, and
AI-enriched event detection over **WebSocket** (dashboards) and **gRPC
streaming** (B2B integrations), consuming any input: **SRT, RTMP, HLS, MP4**.

**Target**: broadcast overlay (<1s latency) + near-live data feed (2-5s).
**Scale target**: 1-2 matches now, architected to scale to 10+ without rewrite.
**Cloud**: Azure, Sweden Central (closest to Viaplay/TV2).

---

## The one-minute pitch

Viaplay and TV2 send us their match feed on any of the four standard protocols.
We decode it in a dedicated microservice, push frames to Azure Cache for Redis,
and have a GPU-backed inference pod run:

1. **YOLOv8m** for person + ball detection
2. **ByteTrack** for persistent player IDs across frames
3. **3-cluster jersey color k-means** to split home / away / referees
4. **Azure AI Vision Read API** to OCR jersey numbers (temporal voting)
5. **Deterministic heuristics** for possession, shot on goal, corner, fast break
6. **GPT-4o** for broadcast-quality natural-language descriptions (rate-limited)

Every frame's result lands in Azure PostgreSQL + TimescaleDB, and fans out to:
- Our Next.js dashboard via WebSocket (demo UI)
- Viaplay/TV2 via **gRPC streaming** with API key auth (their graphics systems)

The whole thing is 3 independent microservices plus Redis and Postgres. Each
scales independently (KEDA on the inference GPU pool based on Redis queue lag).

---

## High-level architecture

```
        SRT / RTMP / HLS / MP4                 WebSocket              gRPC
                  │                                 │                  │
                  ▼                                 │                  │
      ┌─────────────────────┐                       │                  │
      │   vio-ingestion     │                       │                  │
      │  (OpenCV+FFmpeg)    │                       │                  │
      │  → JPEG frames      │                       │                  │
      └──────────┬──────────┘                       │                  │
                 │                                  │                  │
      ┌──────────▼──────────┐                       │                  │
      │  Azure Redis P1     │ ◀─────────────┐       │                  │
      │  frames + pub/sub   │               │       │                  │
      └──────────┬──────────┘               │       │                  │
                 │                          │       │                  │
      ┌──────────▼──────────────────────┐   │       │                  │
      │       vio-inference (GPU)       │   │       │                  │
      │  • YOLOv8m + ByteTrack          │   │       │                  │
      │  • Jersey OCR (Azure Vision)    │   │       │                  │
      │  • Event detector (heuristics)  │   │       │                  │
      │  • GPT-4o enrichment            │   │       │                  │
      └──────────┬──────────────────────┘   │       │                  │
                 │ publishes detections     │       │                  │
                 └──────────────────────────┘       │                  │
                                                    │                  │
      ┌───────────────────────────────────┐         │                  │
      │         vio-gateway               │◀────────┘                  │
      │  :8000 REST + WebSocket  │◀────── (Next.js dashboard)          │
      │  :50051 gRPC             │◀────────────────────────────────────┘
      └──────────┬──────────────┘         (Viaplay / TV2 B2B)
                 │ persist events
                 ▼
      ┌─────────────────────────┐
      │  Azure PostgreSQL       │
      │  + TimescaleDB          │
      │  (sessions, events)     │
      └─────────────────────────┘
```

---

## Outputs Viaplay/TV2 receive

Every detection frame includes:

- **Tracks** — one entry per player with `track_id`, `team` (home/away/ref),
  `jersey_number`, `position` (normalized 0-1), bounding box, team color hex
- **Ball** — position, bbox, confidence
- **Possession** — current team + rolling percentages (home/away %)
- **Event** — one of 18 event types (heuristic + GPT-4o refined) with
  `tension_score`, `description`, `confirmed` flag
- **Crowd intensity** — audio-derived 0-10 scale
- **Sentiment** — calm / tense / euphoric / frustrated (from GPT-4o)

Same protobuf schema (`shared/proto/match_events.proto`) consumable from Go,
Node, Java, C#, etc. No proprietary client libraries required.

---

## What makes this production-grade (vs a hobby demo)

- **Decoupled microservices** — video plane and ML plane separated via Redis
  so each scales independently. Industry-standard pattern (SportRadar, Stats
  Perform).
- **Multi-protocol ingest** — not just HTTP/MP4 like most demos. Real SRT
  support speaks Viaplay/TV2's professional contribution language.
- **Persistent tracking** — ByteTrack maintains player IDs through occlusions
  instead of greedy nearest-neighbor that re-labels every frame.
- **Hybrid intelligence** — heuristics fire in <1ms per frame; GPT-4o only
  runs on significant moments (rate-limited, cost-controlled).
- **B2B-grade delivery** — gRPC streaming with `.proto` contract + API key
  auth. This is what actual broadcasters expect.
- **Observability** — Prometheus metrics on all 3 services; Application
  Insights + Log Analytics for cloud-native tracing.
- **Infrastructure as code** — whole Azure stack provisions with
  `terraform apply` + `bash post-apply.sh`. No portal clicking.

---

## What we delivered (4 sprints)

| Sprint | Focus | Key deliverables |
|--------|-------|-----------------|
| **1** | Foundation | Split monolith into 3 microservices, multi-protocol ingest, YOLO+ByteTrack, jersey color clustering |
| **2** | Intelligence | Event detection heuristics, GPT-4o enrichment rate-limited, Postgres persistence, gRPC server |
| **3** | Production readiness | API key auth, Prometheus metrics, K8s manifests, Terraform, B2B client example, load test |
| **4** (roadmap) | Hardening | TrackNet ball model, homography, multi-region, private networking, TLS, CI/CD |

See `docs/PLAN.md` for the executed sprint-by-sprint detail.

---

## Cost snapshot

| Tier | $/mo | Concurrent matches |
|------|------|--------------------|
| Demo (current) | $1,500-2,000 | 1-3 |
| Production pilot | $3,000-5,000 | 1-10 |
| Multi-region at scale | $15,000-30,000 | 50+ |

With Azure credits, the demo tier runs indefinitely at no out-of-pocket cost.

---

## Quick links

- **Architecture deep-dive**: `docs/INFRASTRUCTURE.md`
- **Execution history**: `docs/PLAN.md`
- **Local dev**: `README.md`
- **Azure deploy**: `infra/terraform/README.md`
- **B2B integration example**: `examples/grpc-client/`
- **gRPC contract**: `shared/proto/match_events.proto`
