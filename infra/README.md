# Runbook de despliegue — aiVeridia Events (AWS us-east-1)

## Prerrequisitos

- Terraform ≥ 1.7, AWS CLI con perfil de la cuenta, Docker con buildx (arm64).
- Starter toolkit de AgentCore: `pip install bedrock-agentcore-starter-toolkit`.
- Bucket de estado `aiveridia-tfstate` creado (una vez, manual).

## 1. Infraestructura base

```bash
cd infra
terraform init
terraform plan  -var aiveridia_events_model_arn="" -var alert_email=ops@aiveridia.com
terraform apply -var aiveridia_events_model_arn="" -var alert_email=ops@aiveridia.com
```

Crea: ECR (2 repos), rol AgentCore, Lambdas de borde + HTTP API, EventBridge bus
(`reserva.confirmada` → resume), Scheduler group (timers BPMN), Secrets, S3 (assets,
finetune, dashboard), CloudFront, alarmas SNS y presupuesto mensual.
`aiveridia_events_model_arn` vacío hasta completar el CMI (ml/README.md §4): el router
degrada a Llama 3.1 8B base.

## 2. Secretos (una vez por entorno)

```bash
aws secretsmanager put-secret-value \
  --secret-id aiveridia-events/prod/app \
  --secret-string '{"DATABASE_URL":"postgresql://...supabase...", "SUPABASE_URL":"...",
    "SUPABASE_SERVICE_KEY":"...", "WHATSAPP_TOKEN":"...", "CULQI_SECRET":"...",
    "LANGSMITH_API_KEY":"...", "AIVERIDIA_EVENTS_MODEL_ARN":""}'
```

## 3. Base de datos (Supabase)

```bash
supabase link --project-ref <ref>
supabase db push          # aplica db/migrations/ en orden
psql "$DATABASE_URL" -f db/seed.sql
make rag-ingest           # conocimiento de Los Jazmines (embeddings Titan)
```

## 4. Grafos en AgentCore Runtime

```bash
cd apps/agents
agentcore configure -e src/agentcore/app_comercial.py \
  --name graph-comercial --execution-role <agentcore_role del output>
agentcore launch          # build arm64 + push ECR + create runtime

agentcore configure -e src/agentcore/app_operativo.py --name graph-operativo ...
agentcore launch
```

Copiar los ARN de ambos runtimes a las variables de entorno de las Lambdas
(`GRAPH_COMERCIAL_RUNTIME_ARN`, `GRAPH_OPERATIVO_RUNTIME_ARN`) — vía Terraform
(`terraform apply` con las vars) o consola en el piloto.

## 5. WhatsApp Business API

En Meta for Developers: webhook = `<webhook_url del output>/whatsapp`, verify token =
`WHATSAPP_VERIFY_TOKEN`, suscripción a `messages`. El `phone_number_id` de cada salón
se registra en `tenants.whatsapp_phone_id` (onboarding = filas nuevas, cero deploy).

## 6. Dashboard

```bash
make dashboard-build
aws s3 sync apps/dashboard/dist "s3://$(terraform -chdir=infra output -raw dashboard_bucket)" --delete
aws cloudfront create-invalidation \
  --distribution-id <id> --paths "/*"
```

## 7. Modelo propio (cuando esté listo el fine-tuning)

ml/README.md §2-4: entrenar → fusionar → CMI → actualizar
`AIVERIDIA_EVENTS_MODEL_ARN` en el secreto. Gate: `make eval-langsmith` con
adherencia a precios = 1.0.

## Rollback

- Grafos: `agentcore launch` re-despliega la imagen anterior (tags inmutables en ECR).
- Modelo: revertir el ARN en el secreto (efecto inmediato, sin redeploy).
- Infra: `terraform apply` de la revisión anterior del repo.

## Verificación post-deploy

1. `GET <webhook_url>/whatsapp?hub.mode=subscribe&...` responde el challenge.
2. Mensaje de WhatsApp de prueba → respuesta del agente + lead en el dashboard.
3. Alarmas en verde y presupuesto activo (SNS confirmado por email).
