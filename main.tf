# ============================================================================
# aiVeridia Events — Infraestructura AWS (Terraform >= 1.7, provider aws ~> 5)
# Región: us-east-1 (Bedrock + AgentCore + Custom Model Import disponibles).
# La latencia Lima -> us-east-1 (~120 ms) es irrelevante: el canal es
# WhatsApp asíncrono, no streaming de voz.
#
# El runtime de los grafos LangGraph se despliega en Bedrock AgentCore
# Runtime a partir de la imagen ECR creada aquí:
#     agentcore configure -e app.py && agentcore launch   (starter toolkit)
# Terraform gestiona todo lo que rodea al runtime: identidad, eventos,
# secretos, colas y almacenamiento.
# ============================================================================

terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
  backend "s3" {
    bucket = "aiveridia-tfstate"
    key    = "events/prod.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = { Project = "aiveridia-events", Env = var.env, Owner = "aiVeridia" }
  }
}

variable "env" { default = "prod" }
variable "aiveridia_events_model_arn" {
  description = "ARN del modelo AiVeridiaEvents importado vía Bedrock Custom Model Import"
  type        = string
}

# ----------------------------------------------------------------------------
# 1. ECR — imágenes de los grafos (comercial P1 y operativo P2)
# ----------------------------------------------------------------------------
resource "aws_ecr_repository" "graphs" {
  for_each             = toset(["graph-comercial", "graph-operativo"])
  name                 = "aiveridia-events/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

# ----------------------------------------------------------------------------
# 2. IAM — rol de ejecución para AgentCore Runtime
# ----------------------------------------------------------------------------
data "aws_caller_identity" "me" {}

resource "aws_iam_role" "agentcore_runtime" {
  name = "aiveridia-events-agentcore-${var.env}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.me.account_id }
      }
    }]
  })
}

resource "aws_iam_role_policy" "agentcore_permissions" {
  name = "runtime-permissions"
  role = aws_iam_role.agentcore_runtime.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeAiVeridiaEventsYFallbacks"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = [
          var.aiveridia_events_model_arn,
          "arn:aws:bedrock:us-east-1::foundation-model/*"
        ]
      },
      {
        Sid      = "LeerSecretos"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.app.arn]
      },
      {
        Sid      = "PublicarEventosDominio"
        Effect   = "Allow"
        Action   = ["events:PutEvents"]
        Resource = [aws_cloudwatch_event_bus.domain.arn]
      },
      {
        Sid      = "ContratosYMedia"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["${aws_s3_bucket.assets.arn}/*"]
      },
      {
        Sid      = "Observabilidad"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream",
                    "logs:PutLogEvents", "xray:PutTraceSegments",
                    "cloudwatch:PutMetricData"]
        Resource = "*"
      }
    ]
  })
}

# ----------------------------------------------------------------------------
# 3. SECRETOS — Supabase, WhatsApp, pasarela, LangSmith
# ----------------------------------------------------------------------------
resource "aws_secretsmanager_secret" "app" {
  name = "aiveridia-events/${var.env}/app"
  # Claves esperadas (se cargan fuera de Terraform):
  #   SUPABASE_URL, SUPABASE_SERVICE_KEY, DATABASE_URL (checkpointer),
  #   WHATSAPP_TOKEN, CULQI_SECRET, LANGSMITH_API_KEY,
  #   AIVERIDIA_EVENTS_MODEL_ARN
}

# ----------------------------------------------------------------------------
# 4. BUS DE EVENTOS DE DOMINIO — "reserva.confirmada" encadena P1 -> P2
# ----------------------------------------------------------------------------
resource "aws_cloudwatch_event_bus" "domain" {
  name = "aiveridia-events-${var.env}"
}

resource "aws_cloudwatch_event_rule" "reserva_confirmada" {
  name           = "reserva-confirmada"
  event_bus_name = aws_cloudwatch_event_bus.domain.name
  event_pattern = jsonencode({
    source        = ["aiveridia.events"]
    "detail-type" = ["reserva.confirmada"]
  })
}

resource "aws_cloudwatch_event_target" "arranca_grafo_operativo" {
  rule           = aws_cloudwatch_event_rule.reserva_confirmada.name
  event_bus_name = aws_cloudwatch_event_bus.domain.name
  arn            = aws_lambda_function.resume.arn
}

# ----------------------------------------------------------------------------
# 5. LAMBDAS DE BORDE — webhooks (WhatsApp, pasarela) y resume de grafos
#    Los event-based gateways del BPMN viven aquí: cada Lambda reanuda el
#    thread correcto del checkpointer con el evento recibido.
# ----------------------------------------------------------------------------
data "archive_file" "lambda_stub" {
  type        = "zip"
  source_dir  = "${path.module}/lambdas"
  output_path = "${path.module}/build/lambdas.zip"
}

resource "aws_iam_role" "lambda" {
  name = "aiveridia-events-lambda-${var.env}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole",
                   Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_invoke_runtime" {
  name = "invoke-agentcore-y-scheduler"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["bedrock-agentcore:InvokeAgentRuntime"],
        Resource = "*" },
      { Effect = "Allow",
        Action = ["scheduler:CreateSchedule", "scheduler:DeleteSchedule"],
        Resource = "*" },
      { Effect = "Allow", Action = ["iam:PassRole"],
        Resource = [aws_iam_role.scheduler.arn] },
      { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"],
        Resource = [aws_secretsmanager_secret.app.arn] }
    ]
  })
}

locals {
  lambdas = {
    webhook_whatsapp = "Recibe mensajes entrantes -> resume(mensaje_cliente|cliente_acepta)"
    webhook_pagos    = "Webhook Culqi/MercadoPago -> resume(pago_ok)"
    resume           = "Objetivo de Scheduler/EventBridge -> resume(timeout_*|start P2)"
  }
}

resource "aws_lambda_function" "webhook_whatsapp" {
  function_name = "aiveridia-events-webhook-whatsapp-${var.env}"
  role          = aws_iam_role.lambda.arn
  handler       = "webhook_whatsapp.handler"
  runtime       = "python3.12"
  filename      = data.archive_file.lambda_stub.output_path
  timeout       = 30
  environment { variables = { SECRET_ARN = aws_secretsmanager_secret.app.arn } }
}

resource "aws_lambda_function" "webhook_pagos" {
  function_name = "aiveridia-events-webhook-pagos-${var.env}"
  role          = aws_iam_role.lambda.arn
  handler       = "webhook_pagos.handler"
  runtime       = "python3.12"
  filename      = data.archive_file.lambda_stub.output_path
  timeout       = 30
  environment { variables = { SECRET_ARN = aws_secretsmanager_secret.app.arn } }
}

resource "aws_lambda_function" "resume" {
  function_name = "aiveridia-events-resume-${var.env}"
  role          = aws_iam_role.lambda.arn
  handler       = "resume.handler"
  runtime       = "python3.12"
  filename      = data.archive_file.lambda_stub.output_path
  timeout       = 60
  environment { variables = { SECRET_ARN = aws_secretsmanager_secret.app.arn } }
}

resource "aws_lambda_permission" "eventbridge_resume" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.resume.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reserva_confirmada.arn
}

# API pública para los webhooks (HTTP API, la opción más barata)
resource "aws_apigatewayv2_api" "webhooks" {
  name          = "aiveridia-events-webhooks-${var.env}"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "wa" {
  api_id                 = aws_apigatewayv2_api.webhooks.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.webhook_whatsapp.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "pagos" {
  api_id                 = aws_apigatewayv2_api.webhooks.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.webhook_pagos.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "wa" {
  api_id    = aws_apigatewayv2_api.webhooks.id
  route_key = "POST /whatsapp"
  target    = "integrations/${aws_apigatewayv2_integration.wa.id}"
}

resource "aws_apigatewayv2_route" "pagos" {
  api_id    = aws_apigatewayv2_api.webhooks.id
  route_key = "POST /pagos"
  target    = "integrations/${aws_apigatewayv2_integration.pagos.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.webhooks.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw_wa" {
  statement_id  = "AllowAPIGwWA"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.webhook_whatsapp.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhooks.execution_arn}/*/*"
}

resource "aws_lambda_permission" "apigw_pagos" {
  statement_id  = "AllowAPIGwPagos"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.webhook_pagos.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhooks.execution_arn}/*/*"
}

# ----------------------------------------------------------------------------
# 6. EVENTBRIDGE SCHEDULER — los timers del BPMN
#    (48 h sin respuesta, 24/48 h sin pago, 7 días antes, día del evento,
#     +1 día, +10 meses). Las Lambdas crean schedules one-shot en este grupo.
# ----------------------------------------------------------------------------
resource "aws_scheduler_schedule_group" "timers" {
  name = "aiveridia-events-timers-${var.env}"
}

resource "aws_iam_role" "scheduler" {
  name = "aiveridia-events-scheduler-${var.env}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole",
                   Principal = { Service = "scheduler.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "invoke-resume"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "lambda:InvokeFunction",
                   Resource = aws_lambda_function.resume.arn }]
  })
}

# ----------------------------------------------------------------------------
# 7. ALMACENAMIENTO — contratos PDF, fotos de paquetes, dataset de fine-tuning
# ----------------------------------------------------------------------------
resource "aws_s3_bucket" "assets" {
  bucket = "aiveridia-events-assets-${var.env}"
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket                  = aws_s3_bucket.assets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket" "finetune" {
  bucket = "aiveridia-events-finetune-${var.env}"
  # dataset ShareGPT + adapters QLoRA fusionados para Custom Model Import
}

# ----------------------------------------------------------------------------
# 8. SALIDAS
# ----------------------------------------------------------------------------
output "webhook_url"       { value = aws_apigatewayv2_stage.default.invoke_url }
output "ecr_repos"         { value = { for k, r in aws_ecr_repository.graphs : k => r.repository_url } }
output "agentcore_role"    { value = aws_iam_role.agentcore_runtime.arn }
output "event_bus"         { value = aws_cloudwatch_event_bus.domain.name }
output "scheduler_group"   { value = aws_scheduler_schedule_group.timers.name }
