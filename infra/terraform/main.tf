terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
  }
}

provider "azurerm" {
  features {}
}

# ─── Inputs ─────────────────────────────────────────────────────────

variable "prefix" {
  default = "viovision"
}

variable "location" {
  # Nearest to Viaplay/TV2 in Scandinavia
  default = "swedencentral"
}

variable "environment" {
  default = "demo"
}

locals {
  name = "${var.prefix}-${var.environment}"
  tags = {
    project     = "vio-vision"
    environment = var.environment
    owner       = "platform"
  }
}

# ─── Resource Group ─────────────────────────────────────────────────

resource "azurerm_resource_group" "rg" {
  name     = "${local.name}-rg"
  location = var.location
  tags     = local.tags
}

# ─── Azure Container Registry ──────────────────────────────────────

resource "azurerm_container_registry" "acr" {
  name                = "${replace(local.name, "-", "")}acr"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "Standard"
  admin_enabled       = true
  tags                = local.tags
}

# ─── AKS Cluster (CPU pool) ────────────────────────────────────────

resource "azurerm_kubernetes_cluster" "aks" {
  name                = "${local.name}-aks"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  dns_prefix          = local.name
  tags                = local.tags

  default_node_pool {
    name       = "system"
    node_count = 2
    vm_size    = "Standard_D4s_v5"
  }

  identity {
    type = "SystemAssigned"
  }
}

# ─── GPU node pool (for vio-inference) ─────────────────────────────

resource "azurerm_kubernetes_cluster_node_pool" "gpu" {
  name                  = "gpu"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
  vm_size               = "Standard_NC4as_T4_v3" # 1x Tesla T4, 4 vCPU, 28GB
  node_count            = 1
  min_count             = 1
  max_count             = 5
  enable_auto_scaling   = true

  node_taints = ["sku=gpu:NoSchedule"]

  tags = local.tags
}

# Grant AKS pull from ACR
resource "azurerm_role_assignment" "aks_acr" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
}

# ─── Azure Cache for Redis (frame bus) ──────────────────────────────

resource "azurerm_redis_cache" "redis" {
  name                = "${local.name}-redis"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  capacity            = 1
  family              = "P" # Premium
  sku_name            = "Premium"
  non_ssl_port_enabled = false
  minimum_tls_version = "1.2"
  tags                = local.tags

  redis_configuration {
    maxmemory_policy = "allkeys-lru"
  }
}

# ─── Postgres Flexible (events store) ───────────────────────────────

resource "random_password" "pg" {
  length  = 20
  special = true
}

resource "azurerm_postgresql_flexible_server" "pg" {
  name                   = "${local.name}-pg"
  resource_group_name    = azurerm_resource_group.rg.name
  location               = azurerm_resource_group.rg.location
  version                = "16"
  administrator_login    = "vio"
  administrator_password = random_password.pg.result
  sku_name               = "GP_Standard_D2ds_v5"
  storage_mb             = 32768
  public_network_access_enabled = true
  zone                   = "1"
  tags                   = local.tags
}

resource "azurerm_postgresql_flexible_server_database" "vio_vision" {
  name      = "vio_vision"
  server_id = azurerm_postgresql_flexible_server.pg.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_postgresql_flexible_server_configuration" "timescale" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.pg.id
  value     = "TIMESCALEDB"
}

# ─── Application Insights ───────────────────────────────────────────

resource "azurerm_log_analytics_workspace" "law" {
  name                = "${local.name}-law"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_application_insights" "appi" {
  name                = "${local.name}-appi"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.law.id
  tags                = local.tags
}

# ─── Outputs ────────────────────────────────────────────────────────

output "acr_login_server" {
  value = azurerm_container_registry.acr.login_server
}

output "aks_name" {
  value = azurerm_kubernetes_cluster.aks.name
}

output "redis_hostname" {
  value = azurerm_redis_cache.redis.hostname
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.pg.fqdn
}

output "postgres_admin_password" {
  value     = random_password.pg.result
  sensitive = true
}

output "appinsights_connection_string" {
  value     = azurerm_application_insights.appi.connection_string
  sensitive = true
}
