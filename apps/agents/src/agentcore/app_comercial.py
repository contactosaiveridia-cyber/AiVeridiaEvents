"""Bedrock AgentCore Runtime entrypoint — grafo comercial (P1).

Despliegue con el starter toolkit desde apps/agents:
    agentcore configure -e src/agentcore/app_comercial.py
    agentcore launch

El runtime recibe los payloads normalizados de las Lambdas de borde
(infra/lambdas/) y ejecuta EXACTAMENTE la misma lógica de borde que el
servicio FastAPI: los helpers compartidos de api/deps.py (dedup con
compensación, clasificación de aceptación, entrega por canal).
"""

from bedrock_agentcore import BedrockAgentCoreApp

from graphs.checkpointer import open_checkpointer
from graphs.graph_comercial import build_graph

app = BedrockAgentCoreApp()
_ctx = {}


def _graph():
    if "graph" not in _ctx:
        _ctx["cm"] = open_checkpointer()
        _ctx["graph"] = build_graph(_ctx["cm"].__enter__())
    return _ctx["graph"]


@app.entrypoint
def invoke(payload: dict, context=None) -> dict:
    from api.deps import (despachar_evento, procesar_webhook_pagos,
                          procesar_webhook_whatsapp)

    tipo = payload.get("tipo")
    graph = _graph()

    if tipo == "webhook_whatsapp":
        return procesar_webhook_whatsapp(graph, payload["body"])

    if tipo == "webhook_pagos":
        respuesta, codigo = procesar_webhook_pagos(
            graph, payload["body_crudo"].encode(), payload.get("firma", ""))
        return {**respuesta, "http_status": codigo}

    if tipo == "resume":
        resultado = despachar_evento(graph, payload["tenant_id"],
                                     payload["lead_id"], payload["evento"],
                                     telefono=payload.get("telefono"))
        return {"status": "ok", "evento": payload["evento"],
                "resultado": resultado.get("resultado")}

    return {"status": "tipo_desconocido"}


if __name__ == "__main__":
    app.run()
