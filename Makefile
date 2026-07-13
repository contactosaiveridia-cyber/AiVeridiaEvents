# aiVeridia Events — developer entrypoints
COMPOSE = docker compose
DATABASE_URL ?= postgresql://postgres:postgres@localhost:5432/aiveridia

.PHONY: dev down db-migrate db-seed test test-db lint finetune deploy

dev:            ## Boot the full local stack (Postgres + Ollama + agents API)
	$(COMPOSE) up -d --build
	@echo "API:    http://localhost:8000/health"
	@echo "Ollama: http://localhost:11434"

down:
	$(COMPOSE) down

db-migrate:     ## Apply migrations to an existing database
	for f in db/migrations/*.sql; do psql "$(DATABASE_URL)" -v ON_ERROR_STOP=1 -f $$f; done

db-seed:        ## Load the Los Jazmines seed
	psql "$(DATABASE_URL)" -v ON_ERROR_STOP=1 -f db/seed.sql

test:           ## Full test suite (requires DATABASE_URL reachable for DB tests)
	cd apps/agents && python -m pytest -v --cov=src --cov-report=term-missing

test-db:        ## Only the database tests (RLS, double-booking, seed, audit)
	cd apps/agents && python -m pytest tests/test_rls_isolation.py tests/test_double_booking.py tests/test_seed.py -v

lint:
	cd apps/agents && ruff check src tests

simulate:       ## Simulador WhatsApp del funnel P1 (interactivo, requiere Ollama)
	cd apps/agents && python -m cli.simulador

simulate-fake:  ## Simulador sin Postgres (tools en memoria) — demo local
	cd apps/agents && AIV_FAKE_DB=1 python -m cli.simulador

dashboard:      ## Dashboard del dueño en modo dev (Vite)
	cd apps/dashboard && npm install && npm run dev

dashboard-build:
	cd apps/dashboard && npm install && npm run build

rag-ingest:     ## Ingesta el conocimiento de Los Jazmines (FAQ, reglas, políticas)
	cd apps/agents && python -m rag.ingesta \
		--tenant 11111111-1111-1111-1111-111111111111 \
		--dir ../../db/conocimiento/los_jazmines

finetune:       ## QLoRA sobre Llama 3.1 8B (requiere GPU o SageMaker; ver ml/README.md)
	cd ml/finetune && axolotl train axolotl_config.yml

eval-golden:    ## Regenera el dataset dorado y valida su consistencia
	cd ml/eval && python generar_dorados.py
	cd apps/agents && python -m pytest tests/test_ml_pipeline.py -q

eval-langsmith: ## Evalúa el modelo activo contra las 50 doradas (LANGSMITH_API_KEY)
	cd ml/eval && python crear_dataset_langsmith.py && python run_eval.py

deploy:         ## Terraform + AgentCore (runbook completo: infra/README.md)
	cd infra && terraform init && terraform apply
	@echo "Grafos: cd apps/agents && agentcore configure && agentcore launch"

tf-validate:
	cd infra && terraform fmt -check -recursive && terraform init -backend=false && terraform validate
