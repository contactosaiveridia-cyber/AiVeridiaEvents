"""Bedrock AgentCore Runtime entrypoint — grafo operativo (P2).

    agentcore configure -e src/agentcore/app_operativo.py
    agentcore launch

Recibe eventos operativos del resume Lambda (Scheduler + bus de dominio):
reserva_confirmada, proveedor_confirma, timeout_proveedor:{id}, pago_cuota,
checkpoint_d7, dia_evento, post_evento, nps_respuesta, campania_renovacion.
"""

from bedrock_agentcore import BedrockAgentCoreApp

from graphs.checkpointer import open_checkpointer
from graphs.graph_operativo import build_graph_operativo

app = BedrockAgentCoreApp()
_ctx = {}


def _graph():
    if "graph" not in _ctx:
        _ctx["cm"] = open_checkpointer()
        _ctx["graph"] = build_graph_operativo(_ctx["cm"].__enter__())
    return _ctx["graph"]


@app.entrypoint
def invoke(payload: dict, context=None) -> dict:
    from api.deps import despachar_evento_p2

    if payload.get("tipo") != "resume":
        return {"status": "tipo_desconocido"}

    evento_raw = payload["evento"]
    base, _, arg = evento_raw.partition(":")
    extra = {"proveedor_id": arg} if arg else None
    if base == "nps_respuesta":
        extra = {"nps_score": payload.get("nps_score"),
                 "nps_comentario": payload.get("nps_comentario")}

    resultado = despachar_evento_p2(_graph(), payload["tenant_id"],
                                    payload["booking_id"], base, payload=extra)
    return {"status": "ok", "evento": base,
            "resultado": resultado.get("resultado")}


if __name__ == "__main__":
    app.run()
