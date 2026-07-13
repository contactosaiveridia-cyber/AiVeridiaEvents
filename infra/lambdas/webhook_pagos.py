"""Edge Lambda: pasarela de pagos -> AgentCore Runtime (graph P1).

La verificación de firma HMAC ocurre DENTRO del runtime (misma lógica que el
servicio FastAPI); esta Lambda solo transporta el cuerpo crudo y la firma."""

import json
import os

import boto3

agentcore = boto3.client("bedrock-agentcore")


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    respuesta = agentcore.invoke_agent_runtime(
        agentRuntimeArn=os.environ["GRAPH_COMERCIAL_RUNTIME_ARN"],
        qualifier="DEFAULT",
        payload=json.dumps({"tipo": "webhook_pagos",
                            "body_crudo": event.get("body") or "",
                            "firma": headers.get("x-aiv-signature", "")}),
    )
    return {"statusCode": 200, "body": respuesta["response"].read().decode()}
