# aiVeridia Events

Plataforma AaaS (Agents-as-a-Service) multi-tenant para gestionar y automatizar salones de
eventos y fiestas infantiles en Perú. Canal principal: WhatsApp Business API. Cliente cero:
salón **Los Jazmines** (Trujillo).

Los procesos de negocio están formalizados en dos BPMN 2.0 ([docs/bpmn/](docs/bpmn/)) que son el
contrato semántico del sistema; la lectura e interpretación adoptada está en
[docs/lectura_bpmn.md](docs/lectura_bpmn.md).

## Estructura del monorepo

```
apps/
  agents/          FastAPI + grafos LangGraph (P1 comercial, P2 operativo)
    src/graphs/    graph_comercial.py, graph_operativo.py
    src/tools/     bookings, pricing, payments, contracts, channels, crm, events
    src/llm/       router get_llm(), prompts por agente
    src/rag/       ingesta + retriever por tenant
    src/core/      config, acceso a BD con contexto de tenant
    src/api/       servicio FastAPI de borde
  dashboard/       React + Vite + Tailwind (dueño del salón)
ml/                dataset, fine-tuning QLoRA, eval LangSmith, guía CMI
infra/             Terraform + Dockerfiles AgentCore
db/                migraciones + seed + verificador embebido
```

## Desarrollo local

```bash
cp .env.example .env
make dev            # Postgres (pgvector) + Ollama + servicio agents
make test           # pytest (los tests de BD requieren el Postgres de compose)
```

Sin Docker también se puede verificar la capa de datos con un Postgres embebido (PGlite):

```bash
cd db/verify && npm install && npm run verify   # migraciones + seed + RLS + constraints
```

(En Windows puede aparecer un `Assertion failed ... async.c` de libuv al terminar: es un
artefacto del teardown de PGlite, el veredicto es la línea `N PASS, M FAIL`.)

## Capa de datos (F1)

- **Patrón pool multi-tenant**: una base, RLS por `tenant_id = current_tenant()` en todas las
  tablas de negocio ([db/migrations/](db/migrations/)). El runtime accede con el rol
  `aiv_agent` (`NOSUPERUSER`, `NOBYPASSRLS`) y fija el tenant por transacción
  ([apps/agents/src/core/db.py](apps/agents/src/core/db.py)).
- **Anti doble-reserva física**: constraint `EXCLUDE USING gist` sobre
  `(espacio_id, tstzrange(inicio, fin))` para estados `hold`/`confirmada` — ningún bug de
  agente puede solapar fechas.
- **Auditoría** (regla innegociable 5): trigger sobre `reservas` registra todo cambio de
  estado en `eventos_auditoria`; ninguna liberación de fecha pasa sin rastro.
- **Métricas del dueño**: la vista materializada `metricas_tenant` no es visible para el rol
  de agentes; se consulta vía `metricas_del_tenant()` (SECURITY DEFINER filtrada por tenant).
- **Seed Los Jazmines**: 3 espacios, 4 paquetes, 7 reglas de precio (temporada / día /
  anticipación / aforo), 10 proveedores ([db/seed.sql](db/seed.sql)).

## Reglas innegociables (resumen)

1. El LLM jamás fija precios, descuentos ni disponibilidad (código determinista + constraints).
2. Descuento > `AIV_UMBRAL_DESCUENTO` (10%) ⇒ `interrupt()` al dueño.
3. Aislamiento por tenant en todas las capas (RLS, RAG, checkpoints, trazas).
4. Webhooks idempotentes (dedup por id de mensaje/transacción).
5. Nunca cancelar sin agotar la escalera de recordatorios; liberaciones auditadas.
6. Secretos solo por env/Secrets Manager.
7. Español peruano hacia el cliente; código y commits en inglés.

## Fases

- [x] **F1 — Fundaciones**: monorepo, compose dev, migraciones + seed, RLS verificada.
- [x] **F2 — Grafo P1** completo + simulador CLI de WhatsApp
      (`make simulate`, o `make simulate-fake` sin Postgres; demo no interactiva:
      `python -m cli.simulador --script demo_funnel.txt`).
- [x] **F3 — Borde y timers**: webhooks WhatsApp/pagos idempotentes (dedup por id en
      `webhook_eventos`), firma HMAC de pasarelas, `/internal/resume` con token, y timers
      one-shot por nodo de espera (APScheduler en dev, EventBridge Scheduler en prod).
- [x] **F4 — RAG por tenant**: ingesta por tenant (`make rag-ingest`, contenido en
      [db/conocimiento/](db/conocimiento/)), embeddings vía router (Titan v2 en prod /
      `nomic-embed-text` local con padding a 1024), retriever con doble candado (RLS +
      filtro explícito) inyectado en A1; fuga cruzada verificada negativa.
- [x] **F5 — Grafo P2**: multi-instancia de proveedores con boundary timer de 48 h por
      instancia y escalación `interrupt()` al dueño, cronograma/cobranza de cuotas,
      checklist D-7 (con RAG), NPS +1 día, métricas A9 y campaña anual a +10 meses;
      encadenado por `reserva.confirmada` (suscriptor en dev, EventBridge en prod).
- [x] **F6 — ML** ([ml/README.md](ml/README.md)): curaduría + anonimización de dataset,
      QLoRA (Axolotl/SageMaker), fusión de adapters, guía Bedrock CMI, Modelfile Ollama,
      y dataset de regresión de 50 conversaciones doradas con evaluadores LangSmith
      (extracción, adherencia 100% a precios, escalamiento, tono).
- [x] **F7 — Dashboard** ([apps/dashboard/](apps/dashboard/)): React + Vite + Tailwind,
      móvil primero (bottom-nav). Login Supabase Auth, embudo de leads, agenda/calendario,
      bandeja de aprobaciones (tabla `aprobaciones` con RLS espejo de los interrupts;
      responder pasa por `POST /owner/aprobaciones/responder`) y métricas A9 vía
      `metricas_del_tenant()`. `make dashboard` para dev.
- [x] **F8 — Infra** ([infra/README.md](infra/README.md)): Terraform completo (alarmas
      CloudWatch + SNS, presupuesto mensual, CloudFront para el dashboard) con
      `terraform validate` limpio; Lambdas de borde; entrypoints y Dockerfiles arm64
      para Bedrock AgentCore Runtime (`agentcore configure && agentcore launch`);
      CI GitHub Actions (ruff + pytest con Postgres real + PGlite + dashboard +
      terraform + docker) y runbook de despliegue.
