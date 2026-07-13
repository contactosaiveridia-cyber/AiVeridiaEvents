"""Edge Lambda: WhatsApp Cloud API webhook -> AgentCore Runtime (graph P1).

Thin adapter: verifies the Meta GET challenge and forwards POST payloads to
the graph-comercial AgentCore runtime, which runs the same edge logic as the
FastAPI service (dedup, tenant resolution, dispatch)."""

import json
import os

import boto3

agentcore = boto3.client("bedrock-agentcore")


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    if event.get("requestContext", {}).get("http", {}).get("method") == "GET":
        if params.get("hub.verify_token") == os.environ["WHATSAPP_VERIFY_TOKEN"]:
            return {"statusCode": 200, "body": params.get("hub.challenge", "")}
        return {"statusCode": 403, "body": "token inválido"}

    respuesta = agentcore.invoke_agent_runtime(
        agentRuntimeArn=os.environ["GRAPH_COMERCIAL_RUNTIME_ARN"],
        qualifier="DEFAULT",
        payload=json.dumps({"tipo": "webhook_whatsapp",
                            "body": json.loads(event.get("body") or "{}")}),
    )
    return {"statusCode": 200, "body": respuesta["response"].read().decode()}
