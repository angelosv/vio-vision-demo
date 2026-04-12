#!/usr/bin/env bash
# Post-apply setup: creates K8s secrets from Terraform outputs + Key Vault.
# Run this after `terraform apply` completes successfully.
#
# Usage:
#   bash post-apply.sh
#
# Prerequisites: az CLI logged in, terraform state present, kubectl configured.

set -euo pipefail

cd "$(dirname "$0")"

NS=vio-demo
KV_NAME=$(terraform output -raw key_vault_name)
AKS_NAME=$(terraform output -raw aks_name)
RG=$(terraform output -raw aks_name | sed 's/-aks/-rg/')
ACR=$(terraform output -raw acr_login_server)

echo "==> Fetching AKS credentials"
az aks get-credentials --resource-group "$RG" --name "$AKS_NAME" --overwrite-existing

echo "==> Creating namespace $NS"
kubectl create namespace $NS --dry-run=client -o yaml | kubectl apply -f -

echo "==> Creating ACR image pull secret"
ACR_NAME=$(echo "$ACR" | cut -d. -f1)
ACR_USER=$(az acr credential show -n "$ACR_NAME" --query username -o tsv)
ACR_PWD=$(az acr credential show -n "$ACR_NAME" --query 'passwords[0].value' -o tsv)
kubectl create secret docker-registry reachuqa2 \
  --docker-server="$ACR" \
  --docker-username="$ACR_USER" \
  --docker-password="$ACR_PWD" \
  -n $NS \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Creating Postgres secret"
PG_DSN=$(az keyvault secret show --vault-name "$KV_NAME" --name postgres-dsn --query value -o tsv)
kubectl create secret generic vio-postgres \
  --from-literal=dsn="$PG_DSN" \
  -n $NS \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Creating API keys secret"
API_KEYS=$(az keyvault secret show --vault-name "$KV_NAME" --name vio-api-keys --query value -o tsv)
kubectl create secret generic vio-api-keys \
  --from-literal=keys="$API_KEYS" \
  -n $NS \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Creating Azure OpenAI secret (if available)"
if az keyvault secret show --vault-name "$KV_NAME" --name openai-endpoint >/dev/null 2>&1; then
  OPENAI_ENDPOINT=$(az keyvault secret show --vault-name "$KV_NAME" --name openai-endpoint --query value -o tsv)
  OPENAI_KEY=$(az keyvault secret show --vault-name "$KV_NAME" --name openai-key --query value -o tsv)
  kubectl create secret generic vio-openai-secret \
    --from-literal=AZURE_OPENAI_ENDPOINT="$OPENAI_ENDPOINT" \
    --from-literal=AZURE_OPENAI_API_KEY="$OPENAI_KEY" \
    -n $NS \
    --dry-run=client -o yaml | kubectl apply -f -
else
  echo "  (OpenAI not provisioned — skip)"
fi

echo "==> Creating Azure AI Vision secret"
VISION_ENDPOINT=$(az keyvault secret show --vault-name "$KV_NAME" --name vision-endpoint --query value -o tsv)
VISION_KEY=$(az keyvault secret show --vault-name "$KV_NAME" --name vision-key --query value -o tsv)
kubectl create secret generic vio-ai-vision \
  --from-literal=endpoint="$VISION_ENDPOINT" \
  --from-literal=key="$VISION_KEY" \
  -n $NS \
  --dry-run=client -o yaml | kubectl apply -f -

echo
echo "==> All secrets created. Now deploy:"
echo "   kubectl apply -f ../../k8s/microservices/"
echo
echo "==> B2B API keys to share with clients:"
echo "   Viaplay: $(terraform output -raw viaplay_api_key)"
echo "   TV2:     $(terraform output -raw tv2_api_key)"
