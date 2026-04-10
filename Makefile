VERSION ?= v0.4.0
ACR = reachuqa2.azurecr.io
NAMESPACE = vio-demo

# ── Local Development ──────────────────────────────────────────────

.PHONY: dev
dev: ## Run frontend locally (connects to Azure backend via .env.local)
	cd web && npm run dev

.PHONY: dev-backend
dev-backend: ## Run backend locally
	cd backend && python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# ── Docker Build ───────────────────────────────────────────────────

.PHONY: build-web
build-web: ## Build frontend Docker image
	docker build -t $(ACR)/vio-vision-web:$(VERSION) web/

.PHONY: build-backend
build-backend: ## Build backend Docker image
	docker build -t $(ACR)/vio-vision-backend:$(VERSION) backend/

.PHONY: build
build: build-web build-backend ## Build both images

# ── Docker Push ────────────────────────────────────────────────────

.PHONY: push-web
push-web: ## Push frontend image to ACR
	docker push $(ACR)/vio-vision-web:$(VERSION)

.PHONY: push-backend
push-backend: ## Push backend image to ACR
	docker push $(ACR)/vio-vision-backend:$(VERSION)

.PHONY: push
push: push-web push-backend ## Push both images

# ── Deploy ─────────────────────────────────────────────────────────

.PHONY: deploy
deploy: ## Apply k8s manifests
	kubectl apply -f k8s/ -n $(NAMESPACE)

.PHONY: rollout
rollout: ## Restart deployments to pull latest images
	kubectl rollout restart deployment/vio-vision-backend -n $(NAMESPACE)
	kubectl rollout restart deployment/vio-vision-web -n $(NAMESPACE)

.PHONY: status
status: ## Show pod status
	kubectl get pods -n $(NAMESPACE)

# ── Full Pipeline ──────────────────────────────────────────────────

.PHONY: ship-web
ship-web: build-web push-web rollout ## Build, push, and rollout frontend

.PHONY: ship
ship: build push deploy rollout ## Build, push, deploy, rollout everything

# ── Help ───────────────────────────────────────────────────────────

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
