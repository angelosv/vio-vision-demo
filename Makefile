VERSION ?= v0.5.0
ACR = reachuqa2.azurecr.io
NAMESPACE = vio-demo

# ── Local Development ──────────────────────────────────────────────

.PHONY: up
up: ## Start full stack locally (redis + 3 services)
	docker compose up --build

.PHONY: up-detached
up-detached: ## Start stack in background
	docker compose up -d --build

.PHONY: down
down: ## Stop local stack
	docker compose down

.PHONY: logs
logs: ## Tail logs from all services
	docker compose logs -f

.PHONY: dev-web
dev-web: ## Run frontend dev server (connects to local stack)
	cd web && npm run dev

# ── Docker Build (Azure) ───────────────────────────────────────────

.PHONY: build-ingestion
build-ingestion:
	docker build -t $(ACR)/vio-ingestion:$(VERSION) vio-ingestion/

.PHONY: build-inference
build-inference:
	docker build -t $(ACR)/vio-inference:$(VERSION) vio-inference/

.PHONY: build-gateway
build-gateway:
	docker build -t $(ACR)/vio-gateway:$(VERSION) vio-gateway/

.PHONY: build-web
build-web:
	docker build -t $(ACR)/vio-vision-web:$(VERSION) web/

.PHONY: build
build: build-ingestion build-inference build-gateway build-web ## Build all images

# ── Push to ACR ────────────────────────────────────────────────────

.PHONY: push-ingestion
push-ingestion:
	docker push $(ACR)/vio-ingestion:$(VERSION)

.PHONY: push-inference
push-inference:
	docker push $(ACR)/vio-inference:$(VERSION)

.PHONY: push-gateway
push-gateway:
	docker push $(ACR)/vio-gateway:$(VERSION)

.PHONY: push-web
push-web:
	docker push $(ACR)/vio-vision-web:$(VERSION)

.PHONY: push
push: push-ingestion push-inference push-gateway push-web ## Push all images

# ── Deploy ─────────────────────────────────────────────────────────

.PHONY: deploy
deploy: ## Apply k8s manifests
	kubectl apply -f k8s/ -n $(NAMESPACE)

.PHONY: rollout
rollout: ## Restart all deployments
	kubectl rollout restart deployment -n $(NAMESPACE)

.PHONY: status
status:
	kubectl get pods -n $(NAMESPACE)

# ── Full Pipeline ──────────────────────────────────────────────────

.PHONY: ship
ship: build push rollout ## Build + push + rollout everything

# ── Help ───────────────────────────────────────────────────────────

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
