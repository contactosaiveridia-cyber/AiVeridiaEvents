# aiVeridia Events — Arquitectura técnica del MVP

Plataforma AaaS multi-tenant para gestión y automatización de salones de eventos y fiestas infantiles. Este documento acompaña a `graph_comercial.py`, `schema_multitenant.sql` y `main.tf`, y a los modelos BPMN P1/P2.

## 1. Topología general

```
WhatsApp / Meta ─► API Gateway ─► Lambda webhook_whatsapp ─┐
Culqi / MercadoPago ─► Lambda webhook_pagos ───────────────┤
EventBridge Scheduler (timers BPMN) ─► Lambda resume ──────┼─► Bedrock AgentCore Runtime
EventBridge Bus "reserva.confirmada" (P1 → P2) ────────────┘      │ Grafo P1 (comercial)
                                                                  │ Grafo P2 (operativo)
                                                                  ▼
                    Bedrock: AiVeridiaEvents (CMI) + fallback Gemini/Claude
                                                                  │
                                Supabase (Postgres + RLS + pgvector)
                                  ├─ datos de negocio multi-tenant
                                  └─ checkpoints LangGraph (DATABASE_URL)
                                                                  │
                                S3 (contratos, media) · Dashboard React (dueño)
```

Decisión estructural clave: **los event-based gateways del BPMN no viven dentro del grafo**. El grafo termina su turno en los nodos de espera; el checkpointer de Postgres persiste el estado; y quien "despierta" al grafo es siempre un evento externo (webhook o Scheduler) que invoca al thread `{tenant_id}:{lead_id}` con el tipo de evento (`cliente_acepta`, `timeout_48h`, `pago_ok`...). Esto hace al sistema serverless de verdad: cero cómputo mientras el cliente piensa.

## 2. AiVeridiaEvents: el LLM propio del vertical

**Base y método.** Llama 3.1 8B Instruct + QLoRA (mismo pipeline ya probado en aiVeridia Academy), adapters fusionados al final para poder importarlo como modelo completo.

**Dataset (el verdadero activo).** Tres fuentes, formato chat multi-turno:
1. Transcripciones reales de ventas por WhatsApp del caso Los Jazmines (anonimizadas), etiquetadas por resultado (convertido/perdido) — se sobre-muestrean las conversaciones ganadoras.
2. Cotizaciones históricas y objeciones frecuentes ("¿incluye torta?", "¿puedo llevar mi decoración?", "está caro") con las respuestas que cerraron venta.
3. Sintético controlado: variaciones generadas con un modelo frontier y validadas por humano, para cubrir registro peruano (Yape/Plin, trato cercano, diminutivos) y casos borde (reprogramaciones, lluvias, cancelaciones).
Meta inicial: 5,000–8,000 ejemplos. Cada nuevo tenant enriquece el dataset → efecto de red del modelo: el moat.

**Qué hace y qué NO hace el modelo.** AiVeridiaEvents redacta, califica, repregunta y persuade. **Nunca** fija precios, ni disponibilidad, ni descuentos: eso es código determinista (`reglas_precio` + constraint `reservas_sin_solape`). Es la implementación literal de "autonomía siempre con frenos".

**Entrenamiento y despliegue.**
- Fine-tuning: SageMaker training job (1× g5.2xlarge, QLoRA 4-bit, ~2–4 h por corrida) o alternativa económica: instancia spot g5 + Axolotl.
- Producción: **Bedrock Custom Model Import** — se sube el modelo fusionado a S3 (`aiveridia-events-finetune`), Bedrock lo sirve serverless y se paga por token, sin GPU 24/7. Ideal para un MVP con tráfico irregular (picos de fin de semana).
- Desarrollo: Ollama local con los mismos adapters (`aiveridia-events:8b`), plantilla de chat idéntica. "El LLM cambia, la chain no": el router en `get_llm()` es el único punto que conoce el entorno.
- Escalación: consultas de razonamiento atípico (negociaciones complejas, reclamos) van a Gemini 2.5 Flash / Claude vía el mismo router.

**Evaluación.** LangSmith con dataset de regresión (50 conversaciones doradas): tasa de extracción correcta de la calificación, adherencia a precios de las reglas (debe ser 100%), tono (LLM-as-judge) y, en producción, la métrica de negocio: conversión lead→reserva por tenant.

## 3. Multi-tenancy

Patrón **pool** (una base, RLS por `tenant_id`) — el mismo de Academy, así se reutiliza middleware y experiencia operativa:
- Toda tabla lleva `tenant_id` con política RLS `tenant_id = current_tenant()`.
- Los agentes fijan el tenant por conexión (`set_config('app.tenant_id', ...)`); el dashboard usa el JWT de Supabase (`tenant_users`).
- El `thread_id` del checkpointer es `{tenant_id}:{lead_id}` → el aislamiento alcanza también a las memorias conversacionales.
- El constraint `EXCLUDE USING gist` en `reservas` es el candado físico anti doble-reserva: ningún bug de agente puede solaparse fechas.
- Branding, tono, paquetes y reglas de precio son datos por tenant, no código: onboarding de un salón nuevo = filas nuevas, cero deploy.

## 4. ¿Qué nube? Análisis comparativo

| Criterio | AWS | GCP | Azure |
|---|---|---|---|
| Runtime de agentes gestionado | **Bedrock AgentCore Runtime** (sesiones aisladas, identidad, observabilidad para agentes de larga duración) | Vertex AI Agent Engine | Azure AI Foundry Agent Service |
| Servir un LLM propio sin GPU dedicada | **Custom Model Import: serverless, pago por token** | Requiere endpoint dedicado con GPU (costo fijo) | Igual: endpoint dedicado |
| Región cercana a Perú | São Paulo (sa-east-1); Bedrock en us-east-1 (~120 ms desde Lima) | **Santiago (~50 ms)** | Brasil |
| Créditos para startups | **AWS Activate (ya explorado por aiVeridia)** | Google for Startups | Microsoft for Startups |
| Continuidad con el stack aiVeridia | **Total: AgentCore, Bedrock, pipeline QLoRA ya dominados** | Parcial (Gemini vía API) | Baja |

**Recomendación: AWS.** GCP gana solo en latencia regional (Santiago), pero el canal es WhatsApp asíncrono donde 120 ms son imperceptibles; y ninguna alternativa ofrece hoy el equivalente a Custom Model Import serverless, que es exactamente lo que un MVP con tráfico irregular necesita para servir AiVeridiaEvents sin pagar GPU ociosa. Sumado a AgentCore Runtime para los grafos y a los créditos de AWS Activate, la decisión es clara. Supabase se mantiene como capa de datos gestionada (región AWS us-east-1, mínima latencia hacia el runtime).

## 5. Costos estimados del MVP (5 tenants piloto)

| Componente | Estimado mensual |
|---|---|
| AiVeridiaEvents vía CMI (pago por token, ~30k conversaciones-turno) | $40–90 |
| AgentCore Runtime (consumo por sesión) | $20–60 |
| Lambdas + API Gateway + Scheduler + EventBridge | < $10 |
| Supabase Pro | $25 |
| WhatsApp Business API (conversaciones iniciadas por empresa) | $30–80 |
| **Total** | **~$125–265/mes** |

Contra un ingreso de 5 tenants × S/ 299 + fees por reserva (~S/ 2,500–4,000/mes ≈ $670–1,070), el margen bruto del piloto ya es positivo desde el mes uno.

## 6. Roadmap de implementación (8 semanas)

1. **S1–2:** esquema SQL + seed de Los Jazmines; webhook WhatsApp; grafo P1 con Ollama local.
2. **S3–4:** reglas de precio reales; interrupt de descuentos al dueño; pasarela de pagos; timers con Scheduler.
3. **S5:** curaduría del dataset v1 (Los Jazmines) + fine-tuning QLoRA + eval en LangSmith.
4. **S6:** Custom Model Import; `terraform apply`; `agentcore launch` de P1.
5. **S7:** grafo P2 (proveedores, cobranza, NPS, campaña anual); dashboard React mínimo.
6. **S8:** piloto pagado con Los Jazmines + 2 salones más de Trujillo; medición de conversión base vs. agente.
