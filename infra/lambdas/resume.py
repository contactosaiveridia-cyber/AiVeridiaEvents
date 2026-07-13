"""Resume Lambda: objetivo de EventBridge Scheduler (timers BPMN) y de la
regla reserva.confirmada (encadena P1 -> P2).

Entradas:
  Scheduler:   {"tenant_id", "lead_id"|"booking_id", "evento"}
  EventBridge: {"detail-type": "reserva.confirmada", "detail": {...}}
Enruta al runtime del grafo correcto según el evento (misma tabla que
api/deps.rutear_evento)."""

import json
import os

import boto3

agentcore = boto3.client("bedrock-agentcore")

EVENTOS_P2 = {"reserva_confirmada", "proveedor_confirma", "timeout_proveedor",
              "pago_cuota", "checkpoint_d7", "dia_evento", "post_evento",
              "nps_respuesta", "campania_renovacion"}


def handler(event, context):
    if event.get("detail-type") == "reserva.confirmada":     # bus de dominio
        detalle = event["detail"]
        payload = {"tipo": "resume", "evento": "reserva_confirmada",
                   "tenant_id": detalle["tenant_id"],
                   "booking_id": detalle["booking_id"]}
    else:                                                    # Scheduler one-shot
        payload = {"tipo": "resume", **event}

    base = payload["evento"].partition(":")[0]
    arn = (os.environ["GRAPH_OPERATIVO_RUNTIME_ARN"] if base in EVENTOS_P2
           else os.environ["GRAPH_COMERCIAL_RUNTIME_ARN"])

    respuesta = agentcore.invoke_agent_runtime(
        agentRuntimeArn=arn, qualifier="DEFAULT", payload=json.dumps(payload))
    return {"statusCode": 200, "body": respuesta["response"].read().decode()}
