# Azure Infrastructure (Terraform)

Provisions the minimal production-grade Azure resources for Vio Vision:

- Resource group in **Sweden Central** (closest to Viaplay/TV2)
- Azure Container Registry (Standard)
- AKS cluster with CPU node pool + **GPU node pool** (NC4as_T4_v3, autoscale 1-5)
- Azure Cache for Redis **Premium** P1 (6GB) for frame bus
- Azure Database for PostgreSQL Flexible (D2ds_v5, 32GB) with TimescaleDB
- Log Analytics workspace + Application Insights

## Usage

```bash
az login
az account set --subscription "<sub-id>"

cd infra/terraform
terraform init
terraform plan
terraform apply
```

## After apply

```bash
# 1. Get AKS credentials
az aks get-credentials -g viovision-demo-rg -n viovision-demo-aks

# 2. Create image pull secret (use ACR admin creds)
az acr credential show -n viovisiondemoacr
kubectl create secret docker-registry reachuqa2 \
  --docker-server=<acr>.azurecr.io \
  --docker-username=<user> \
  --docker-password=<pwd> \
  -n vio-demo

# 3. Create Postgres DSN secret
POSTGRES_DSN="postgresql://vio:$(terraform output -raw postgres_admin_password)@$(terraform output -raw postgres_fqdn):5432/vio_vision?sslmode=require"
kubectl create secret generic vio-postgres --from-literal=dsn="$POSTGRES_DSN" -n vio-demo

# 4. Create API keys secret
kubectl create secret generic vio-api-keys \
  --from-literal=keys="viaplay_$(openssl rand -hex 16):viaplay,tv2_$(openssl rand -hex 16):tv2" \
  -n vio-demo

# 5. Create Azure OpenAI + AI Vision secrets (requires pre-provisioned services)
kubectl create secret generic vio-openai-secret \
  --from-literal=AZURE_OPENAI_ENDPOINT="https://<your>.openai.azure.com" \
  --from-literal=AZURE_OPENAI_API_KEY="<key>" \
  -n vio-demo

kubectl create secret generic vio-ai-vision \
  --from-literal=endpoint="https://<your>.cognitiveservices.azure.com" \
  --from-literal=key="<key>" \
  -n vio-demo

# 6. Deploy microservices
kubectl apply -f ../../k8s/microservices/
```

## Cost estimate (~$1,500-2,000/month)

| Resource | SKU | ~$/mo |
|----------|-----|-------|
| AKS system pool (2x D4s_v5) | Standard | $280 |
| AKS GPU pool (1x NC4as_T4_v3) | Pay as you go | $450 |
| Redis Premium P1 | 6GB | $400 |
| Postgres Flexible GP_D2ds_v5 | 32GB | $160 |
| ACR Standard | — | $20 |
| App Insights + Log Analytics | 1GB/day | $50 |

GPU pool scales to 0-5 nodes based on load (only pays for what's used).

## Tear down

```bash
terraform destroy
```
