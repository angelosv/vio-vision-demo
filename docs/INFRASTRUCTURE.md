# Infrastructure Overview

How Vio Vision is deployed on Microsoft Azure, end to end.

## Regions & geography

Primary region: **Sweden Central** (Stockholm).

Chosen because Viaplay (Sweden/Denmark/Norway/Finland) and TV2 (Denmark/Norway)
have their operations in Scandinavia — minimizes RTT on WebSocket and gRPC
streams. Sweden Central also has confirmed availability of:

- GPU VMs (`NC4as_T4_v3` with Tesla T4)
- Azure OpenAI with `gpt-4o` deployments
- Azure AI Vision S1 tier
- Premium Redis Cache

Secondary (roadmap): **West Europe** (Amsterdam) for DR and latency fallback.

---

## Full resource inventory

### Provisioned automatically by Terraform

| Category | Resource | SKU / Size | Purpose |
|----------|----------|------------|---------|
| **Compute** | AKS cluster | — | Kubernetes control plane |
|              | System node pool | 2 × `Standard_D4s_v5` | Ingestion, gateway, Redis, supporting workloads |
|              | GPU node pool | `Standard_NC4as_T4_v3` autoscale 1-5 | Inference workers with Tesla T4 |
| **Registry** | Azure Container Registry | Standard | Docker images for all 3 services + frontend |
| **Cache**    | Azure Cache for Redis | Premium P1 (6 GB) | Frame bus + pub/sub between services |
| **DB**       | PostgreSQL Flexible Server | `GP_Standard_D2ds_v5`, 32 GB | Sessions + events store |
|              | TimescaleDB extension | — | Hypertable on `events.created_at` |
| **AI**       | Azure OpenAI | S0 | Cognitive account |
|              | gpt-4o deployment | 30 K TPM | Event enrichment model |
|              | Azure AI Vision | S1 | Jersey number OCR |
| **Secrets**  | Azure Key Vault | Standard | DSN, API keys, AI endpoints/keys |
| **Monitor**  | Log Analytics workspace | PerGB2018, 30-day retention | All cluster + service logs |
|              | Application Insights | Workspace-based | APM, traces, metrics |

### Still provisioned manually (not yet in Terraform)

| Resource | Why manual | When to add |
|----------|-----------|-------------|
| Azure AI Video Indexer | Public preview, unstable schema | When scoreboard OCR becomes a requirement |
| Custom domain + TLS cert | Depends on external DNS provider | Production launch |
| SRT listener VM / Container App | Needs public UDP, separate from AKS | When Viaplay/TV2 switch from HLS to SRT contribution |
| Virtual Network + private endpoints | Optional; adds ~$150/mo | Production hardening |

---

## Data flow through the infrastructure

### Happy path: a 90-minute match

```
t=0s     Client POSTs /api/start { "url": "srt://stadium.viaplay/match123" }
         ┗━▶ vio-gateway forwards to vio-ingestion
             ┗━▶ vio-ingestion opens SRT reader, publishes to Redis
                 ┗━▶ Redis key `session:abc123:frames` receives pub/sub announcement
                     ┗━▶ vio-inference pattern-subscriber picks up the session
                         ┗━▶ Spawns InferenceSession with YOLO tracker, OCR, event detector
                             ┗━▶ Kicks off background audio extraction (ffmpeg → RMS)

t=0.1s   First frame arrives in Redis (session:abc123:frame:0, JPEG, TTL 10s)
         ┗━▶ Inference worker decodes, runs YOLO + ByteTrack
             ┗━▶ 3-cluster team classification, tension=0, no event
                 ┗━▶ Publishes to Redis `session:abc123:detections`
                     ┗━▶ vio-gateway listener consumes, persists to Postgres
                         (fire-and-forget via asyncio.create_task)
                         ┗━▶ Broadcasts to all WebSocket clients
                         ┗━▶ gRPC streaming clients also get the MatchFrame

t=15s    Ball speeds up toward the left goal
         ┗━▶ Event detector emits { type: "shot_on_goal", tension: 3 }
             ┗━▶ Tension rolls into the window, GPT-4o enrichment triggers
                 ┗━▶ Azure OpenAI call (async, 200-500ms)
                     ┗━▶ Returns { type: "goal_chance", description: "...", sentiment: "tense" }
                         ┗━▶ Merged with heuristic, published downstream

t=30s    Audio analyzer finishes background extraction
         ┗━▶ crowd_intensity now populated in subsequent payloads

t=90min  Video EOF
         ┗━▶ ingestion publishes { type: "end" }
             ┗━▶ inference worker ends session
             ┗━▶ gateway persists session.end_time, duration, status=completed
             ┗━▶ WebSocket clients receive { status: "finished" }
```

### Scaling behavior

- **Ingestion pods** scale horizontally with the number of concurrent
  sessions (each pod handles multiple sessions in parallel via async).
- **Inference GPU pods** autoscale 1-5 with KEDA based on Redis queue
  length (roadmap; currently manual).
- **Gateway pods** (2 replicas) share Redis pub/sub subscriptions — the
  manager dedupes listeners per session_id.
- **Postgres** handles ~100-500 events/session; events are small
  (~300 bytes each). A typical match produces ~50 events persisted
  (non-normal-play or tension ≥ 3).

---

## Networking topology

### Current (demo tier)

```
Public Internet
     │
     ▼
┌─────────────────────────────────────────┐
│  AKS LoadBalancer (nginx-ingress TBD)   │
│  Public IP: <dynamic>                   │
└─────────┬───────────────┬───────────────┘
          │ :8000 REST/WS │ :50051 gRPC
          ▼               ▼
       vio-gateway (ClusterIP, 2 replicas)
          │
          ▼ (cluster DNS)
       vio-ingestion ClusterIP
       vio-inference ClusterIP
       vio-redis    ClusterIP (StatefulSet)
          │
          ▼ (cluster DNS via Azure)
      Redis Premium  ←── private link (TLS 1.2 only)
      Postgres       ←── public endpoint with FW rule allowing AKS outbound IP
      Azure OpenAI   ←── public endpoint + key auth
      AI Vision      ←── public endpoint + key auth
```

### Production target (Sprint 4+)

```
Public Internet → Azure Front Door → AKS (private IP only)
                                           │
                                           ▼
                                    Azure Private Link:
                                       • Redis
                                       • Postgres
                                       • Key Vault
                                       • Cognitive Services
```

---

## Secrets management

Three sources of secrets in the cluster:

1. **ACR pull secret** (`reachuqa2`) — created by `post-apply.sh` from
   ACR admin credentials. Mounted as `imagePullSecrets`.

2. **Auto-generated** by Terraform and stored in Key Vault:
   - `postgres-dsn` — full DSN with random password
   - `vio-api-keys` — `viaplay_<hex>:viaplay,tv2_<hex>:tv2`
   - `openai-endpoint` + `openai-key`
   - `vision-endpoint` + `vision-key`

3. **K8s Secret objects** — `post-apply.sh` materializes Key Vault
   contents into Kubernetes secrets:
   - `vio-postgres` (DSN)
   - `vio-api-keys` (keys)
   - `vio-openai-secret`
   - `vio-ai-vision`

The pods use `secretKeyRef` with `optional: true` so missing secrets
degrade gracefully (e.g., OpenAI off → inference still produces
heuristic events).

Roadmap: replace option (3) with **Azure AD Workload Identity + CSI
driver for Key Vault** so secrets are never written to etcd.

---

## Monitoring & observability

Three layers, each with a specific purpose:

| Layer | Source | Goes where | Used for |
|-------|--------|------------|----------|
| **Container logs** | stdout/stderr | Log Analytics via AKS Container Insights | Debugging, incident post-mortems |
| **Prometheus metrics** | `/metrics` on each service | Scraped by Azure Managed Prometheus (future) | Dashboards, alerting, SLO tracking |
| **Application Insights** | `opencensus` / OTLP SDK (future) | Application Insights | Distributed traces across the 3-hop pipeline |

### Key metrics

| Metric | Service | SLO suggestion |
|--------|---------|----------------|
| `vio_inference_frame_latency_seconds` | inference | p95 < 250ms |
| `vio_gateway_grpc_stream_frames_total` | gateway | > 0 during active session |
| `vio_gateway_events_persisted_total` | gateway | matches expected per session |
| `vio_ingestion_errors_total` | ingestion | < 1 per minute |
| `vio_inference_ai_enrichments_total` | inference | < 20 per minute (rate control) |

### Alerting (roadmap)

- GPU pool scale at max for > 5 min → alert
- Postgres connection pool exhausted → alert
- GPT-4o 429 responses > 10/min → alert + kill switch
- SRT ingestion reconnect count > 5 in session → alert

---

## Cost model

### Demo tier (what we have now)

| Resource | Monthly cost |
|----------|--------------|
| AKS system pool (2x D4s_v5) | $280 |
| AKS GPU (1x NC4as_T4_v3, 50% util) | $225 |
| Redis Premium P1 (6GB) | $400 |
| Postgres Flexible D2ds_v5 | $160 |
| ACR Standard | $20 |
| Azure OpenAI pay-as-you-go | $50-200 |
| Azure AI Vision S1 | $30-100 |
| Key Vault | $3 |
| Log Analytics + App Insights | $30-80 |
| **Total** | **~$1,500-2,000** |

### Production pilot (1-10 concurrent matches)

| Change from demo | Monthly delta |
|-----------------|---------------|
| GPU pool 3 nodes average | +$450 |
| Redis Premium P2 (13GB) | +$400 |
| Postgres GP_D4ds_v5 | +$160 |
| Private networking (VNet, private endpoints) | +$150 |
| **New total** | **~$3,000-5,000** |

### Multi-region at scale (50+ matches)

Multiply primary region by 2 for DR + add geo-replication:

- 2x GPU pools with 5-10 nodes each
- Redis geo-replication (Enterprise tier)
- Postgres HA + read replicas
- Azure Front Door for global entry

**Total: ~$15,000-30,000/mo**

---

## Deployment workflow (summary)

```bash
# ─── One-time setup ──────────────────────────────────────────────────
az login
az account set -s <subscription-id>
# Apply for Azure OpenAI access at https://aka.ms/oai/access
# Request GPU quota in Sweden Central

# ─── Provision (~15 minutes) ─────────────────────────────────────────
cd infra/terraform
terraform init
terraform apply

# ─── Configure K8s secrets (~1 minute) ───────────────────────────────
bash post-apply.sh
# → prints the Viaplay + TV2 API keys to share with clients

# ─── Build + push images ─────────────────────────────────────────────
cd ../..
make build
make push

# ─── Deploy microservices ────────────────────────────────────────────
kubectl apply -f k8s/microservices/
kubectl get pods -n vio-demo -w

# ─── Smoke test ──────────────────────────────────────────────────────
GATEWAY=$(kubectl get svc vio-gateway -n vio-demo -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
curl http://$GATEWAY:8000/api
# → {"status":"ok","service":"vio-gateway","api_version":"0.5.0"}

# ─── Stream a match ──────────────────────────────────────────────────
curl -X POST http://$GATEWAY:8000/api/start \
  -H "Content-Type: application/json" \
  -d '{"url":"https://...sample.mp4"}'
# → {"session_id":"abc12345","source_type":"mp4","status":"started"}

# ─── Watch the gRPC feed ─────────────────────────────────────────────
cd examples/grpc-client
python demo.py stream abc12345 --api-key <viaplay-key>
```
