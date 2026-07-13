# ============================================================================
# Observabilidad y control de costos (completa main.tf).
# - SNS de alertas con suscripción por email.
# - Alarmas: errores por Lambda, 5xx del API Gateway, throttles del Scheduler.
# - Presupuesto mensual con avisos al 80% y 100% (el MVP debe costar
#   ~$125-265/mes según arquitectura_mvp.md §5).
# ============================================================================

variable "alert_email" {
  description = "Correo que recibe alarmas y avisos de presupuesto"
  type        = string
  default     = "ops@aiveridia.com"
}

variable "presupuesto_mensual_usd" {
  type    = number
  default = 300
}

resource "aws_sns_topic" "alertas" {
  name = "aiveridia-events-alertas-${var.env}"
}

resource "aws_sns_topic_subscription" "alertas_email" {
  topic_arn = aws_sns_topic.alertas.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ── Errores por Lambda de borde ──────────────────────────────────────────────
locals {
  lambdas_monitoreadas = {
    webhook_whatsapp = aws_lambda_function.webhook_whatsapp.function_name
    webhook_pagos    = aws_lambda_function.webhook_pagos.function_name
    resume           = aws_lambda_function.resume.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_errores" {
  for_each            = local.lambdas_monitoreadas
  alarm_name          = "aiveridia-${var.env}-${each.key}-errores"
  alarm_description   = "Errores en la Lambda ${each.key} (el funnel puede estar caído)"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alertas.arn]
  ok_actions          = [aws_sns_topic.alertas.arn]
}

# ── 5xx del API de webhooks ──────────────────────────────────────────────────
resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "aiveridia-${var.env}-webhooks-5xx"
  alarm_description   = "Meta reintenta ante 5xx: si persiste, se pierden leads"
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  dimensions          = { ApiId = aws_apigatewayv2_api.webhooks.id }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alertas.arn]
}

# ── Timers BPMN que no logran invocar el resume ─────────────────────────────
resource "aws_cloudwatch_metric_alarm" "scheduler_fallidos" {
  alarm_name          = "aiveridia-${var.env}-timers-fallidos"
  alarm_description   = "Un timer BPMN (48h/7d/pagos) no pudo disparar su evento"
  namespace           = "AWS/Scheduler"
  metric_name         = "InvocationDroppedCount"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alertas.arn]
}

# ── Presupuesto (billing alarm) ──────────────────────────────────────────────
resource "aws_budgets_budget" "mensual" {
  name         = "aiveridia-events-${var.env}"
  budget_type  = "COST"
  limit_amount = tostring(var.presupuesto_mensual_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
