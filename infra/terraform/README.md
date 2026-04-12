# Azure Infrastructure

All production resources for Vio Vision, defined as Terraform.

## Complete list of Azure resources

### Provisioned by Terraform automatically

| Resource | SKU | Purpose |
|----------|-----|---------|
| Resource Group | — | `viovision-demo-rg` in Sweden Central |
| Azure Container Registry | Standard | Docker images |
| AKS Cluster | — | System node pool (2x D4s_v5) |
| AKS GPU Node Pool | NC4as_T4_v3 | Inference (autoscale 1-5) |
| Azure Cache for Redis | Premium P1 (6GB) | Frame bus + pub/sub |
| PostgreSQL Flexible | GP_D2ds_v5 (32GB) | Events store |
| TimescaleDB extension | — | Hypertable for events |
| Azure Key Vault | Standard | Secret store (DSN, API keys, AI endpoints) |
| Azure OpenAI | S0 | GPT-4o event enrichment |
| GPT-4o deployment | 30K TPM | Model capacity |
| Azure AI Vision | S1 | Jersey number OCR |
| Log Analytics workspace | PerGB2018 | Logs |
| Application Insights | — | APM + metrics |

### Prerequisites (do BEFORE `terraform apply`)

1. **Azure subscription** with Contributor role
2. **Azure OpenAI access approval** — https://aka.ms/oai/access
   Without this, set `enable_openai = false` in terraform vars (GPT-4o
   enrichment will be disabled but everything else works).
3. **az CLI** logged in: `az login && az account set -s <sub-id>`

### Quotas to request upfront

Submit these through Azure Portal → Subscriptions → Usage + quotas:

- **GPU quota**: at least 4 vCPUs of `Standard NCASv3_T4 Family` in Sweden Central
- **Azure OpenAI**: 30K TPM for gpt-4o in the OpenAI resource
- **Public IPs**: 2-3 for AKS ingress

## Usage

### 1. Plan + apply

```bash
az login
az account set --subscription "<sub-id>"

cd infra/terraform
terraform init
terraform plan
terraform apply     # takes ~15 minutes
```

### 2. Post-apply setup

The script below fetches AKS credentials, creates the `vio-demo` namespace,
and populates all K8s secrets from Terraform outputs + Key Vault.

```bash
bash post-apply.sh
```

Prints the Viaplay/TV2 API keys at the end — save these securely.

### 3. Build and push images

```bash
cd ../..
make build  # all 4 images
make push   # to your ACR (update ACR var in Makefile first)
```

### 4. Deploy microservices

```bash
kubectl apply -f k8s/microservices/
kubectl get pods -n vio-demo
```

## What still needs manual setup (out of Terraform scope)

### Azure AI Video Indexer (optional, for scoreboard OCR)

Not yet provisionable via Terraform (preview). Manual:

```bash
# In Azure Portal:
# 1. Create "Azure AI Video Indexer" resource
# 2. Link to existing Storage Account (or let it create one)
# 3. Copy Account ID + primary key
# 4. Add to Key Vault:
az keyvault secret set --vault-name $KV_NAME --name vi-account-id --value "<guid>"
az keyvault secret set --vault-name $KV_NAME --name vi-api-key --value "<key>"
```

### Custom domain + TLS

AKS gets a default Azure DNS name. For production:

```bash
# 1. Install nginx-ingress or istio
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx -n ingress-nginx --create-namespace

# 2. Install cert-manager for Let's Encrypt
helm upgrade --install cert-manager jetstack/cert-manager -n cert-manager --create-namespace --set crds.enabled=true

# 3. Point your DNS (e.g. api.viovision.io) to the ingress public IP
# 4. Create Ingress + Certificate resources (see k8s/microservices/ingress-example.yaml)
```

### SRT listener (for live broadcast contribution)

Not in the basic stack. For real SRT ingest from Viaplay/TV2:

```bash
# Option A: Run srt-live-server sidecar pod
# Option B: Use Azure Container Apps with public SRT endpoint
# Option C: Run on separate VM with public IP + UDP port open
```

See `k8s/microservices/srt-listener.yaml` (roadmap item).

### Private networking (production-grade)

The current stack uses public endpoints for Redis and Postgres. For prod:

1. Create a VNet via Terraform (add `virtual_network.tf`)
2. Switch Redis to `Standard` with VNet injection
3. Switch Postgres to private endpoint
4. Configure AKS with Advanced Networking (Azure CNI)

This adds ~$150/mo for the VNet gateway and Private DNS Zones.

## Cost estimate

**Demo / pilot (current config)**: **~$1,500-2,000 /mo**

| Resource | ~$/mo |
|----------|-------|
| AKS system pool (2x D4s_v5) | $280 |
| AKS GPU pool (1x NC4as_T4_v3 running 50% of time) | $225 |
| Redis Premium P1 (6GB) | $400 |
| Postgres Flexible GP_D2ds_v5 | $160 |
| ACR Standard | $20 |
| Azure OpenAI (gpt-4o pay-as-you-go, rate-limited) | $50-200 |
| Azure AI Vision S1 (pay per transaction) | $30-100 |
| Key Vault | $3 |
| App Insights + Log Analytics | $30-80 |

**Production (1-10 concurrent matches)**: **~$3,000-5,000 /mo** — scale GPU pool up to 3-5 nodes, Redis P2, Postgres GP_D4, add private networking.

**At scale (50+ matches, multi-region)**: **~$15,000-30,000 /mo** — multiple GPU pools, Redis geo-replication, Postgres HA.

## Tear down

```bash
terraform destroy
```

⚠️ **Key Vault has 7-day soft delete** — name reuse blocked for that period unless you purge.
