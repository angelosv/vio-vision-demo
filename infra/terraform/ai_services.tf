# Azure AI services (OpenAI, AI Vision) + Key Vault + secure networking.
# These are separated from main.tf to keep the resource list organized.

# ─── Key Vault (secrets store) ──────────────────────────────────────

data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "kv" {
  name                       = "${substr(replace(local.name, "-", ""), 0, 20)}kv"
  location                   = azurerm_resource_group.rg.location
  resource_group_name        = azurerm_resource_group.rg.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = false
  soft_delete_retention_days = 7
  tags                       = local.tags

  access_policy {
    tenant_id = data.azurerm_client_config.current.tenant_id
    object_id = data.azurerm_client_config.current.object_id
    secret_permissions = ["Get", "List", "Set", "Delete", "Purge", "Recover"]
  }

  # Grant AKS kubelet identity access
  access_policy {
    tenant_id = data.azurerm_client_config.current.tenant_id
    object_id = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
    secret_permissions = ["Get", "List"]
  }
}

# Store auto-generated secrets
resource "azurerm_key_vault_secret" "postgres_dsn" {
  name         = "postgres-dsn"
  value        = "postgresql://vio:${random_password.pg.result}@${azurerm_postgresql_flexible_server.pg.fqdn}:5432/vio_vision?sslmode=require"
  key_vault_id = azurerm_key_vault.kv.id
}

resource "random_password" "viaplay_key" {
  length  = 32
  special = false
}

resource "random_password" "tv2_key" {
  length  = 32
  special = false
}

resource "azurerm_key_vault_secret" "api_keys" {
  name         = "vio-api-keys"
  value        = "viaplay_${random_password.viaplay_key.result}:viaplay,tv2_${random_password.tv2_key.result}:tv2"
  key_vault_id = azurerm_key_vault.kv.id
}

# ─── Azure OpenAI (GPT-4o) ─────────────────────────────────────────
# NOTE: Requires Microsoft approval for the subscription.
# Apply for access at https://aka.ms/oai/access before running this.

variable "enable_openai" {
  default     = true
  description = "Set to false if your subscription lacks Azure OpenAI access"
}

resource "azurerm_cognitive_account" "openai" {
  count                 = var.enable_openai ? 1 : 0
  name                  = "${local.name}-openai"
  location              = "swedencentral" # GPT-4o availability region
  resource_group_name   = azurerm_resource_group.rg.name
  kind                  = "OpenAI"
  sku_name              = "S0"
  custom_subdomain_name = "${local.name}-openai"
  tags                  = local.tags
}

resource "azurerm_cognitive_deployment" "gpt4o" {
  count                = var.enable_openai ? 1 : 0
  name                 = "gpt-4o"
  cognitive_account_id = azurerm_cognitive_account.openai[0].id
  model {
    format  = "OpenAI"
    name    = "gpt-4o"
    version = "2024-08-06"
  }
  sku {
    name     = "Standard"
    capacity = 30 # TPM in thousands (30K tokens/min — generous for rate-limited enrichment)
  }
}

resource "azurerm_key_vault_secret" "openai_endpoint" {
  count        = var.enable_openai ? 1 : 0
  name         = "openai-endpoint"
  value        = azurerm_cognitive_account.openai[0].endpoint
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "openai_key" {
  count        = var.enable_openai ? 1 : 0
  name         = "openai-key"
  value        = azurerm_cognitive_account.openai[0].primary_access_key
  key_vault_id = azurerm_key_vault.kv.id
}

# ─── Azure AI Vision (jersey OCR) ──────────────────────────────────

resource "azurerm_cognitive_account" "vision" {
  name                = "${local.name}-vision"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  kind                = "ComputerVision"
  sku_name            = "S1"
  tags                = local.tags
}

resource "azurerm_key_vault_secret" "vision_endpoint" {
  name         = "vision-endpoint"
  value        = azurerm_cognitive_account.vision.endpoint
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "vision_key" {
  name         = "vision-key"
  value        = azurerm_cognitive_account.vision.primary_access_key
  key_vault_id = azurerm_key_vault.kv.id
}

# ─── Outputs for post-apply secret creation ─────────────────────────

output "key_vault_name" {
  value = azurerm_key_vault.kv.name
}

output "openai_endpoint" {
  value = var.enable_openai ? azurerm_cognitive_account.openai[0].endpoint : "(disabled)"
}

output "vision_endpoint" {
  value = azurerm_cognitive_account.vision.endpoint
}

output "viaplay_api_key" {
  value     = "viaplay_${random_password.viaplay_key.result}"
  sensitive = true
}

output "tv2_api_key" {
  value     = "tv2_${random_password.tv2_key.result}"
  sensitive = true
}
