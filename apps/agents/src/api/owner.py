"""Owner-facing endpoints for the dashboard (F7).

El dashboard LEE la bandeja directamente de Supabase (tabla aprobaciones, RLS
por tenant_users); esta API solo ejecuta la ACCIÓN de responder, porque
reanudar el thread de LangGraph requiere el runtime.

Auth MVP: X-Internal-Token compartido (el dashboard lo recibe por env). En
producción se valida el JWT de Supabase del dueño (F8, API Gateway authorizer).
"""

import logging

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from core.config import settings
from graphs import runtime

log = logging.getLogger("aiveridia.api.owner")
router = APIRouter()


def _autorizar(token: str) -> None:
    if token != settings.aiv_internal_token:
        raise HTTPException(401, "token inválido")


@router.get("/owner/aprobaciones")
def listar(tenant_id: str, x_internal_token: str = Header(default="")):
    _autorizar(x_internal_token)
    from tools.aprobaciones import pendientes
    return {"aprobaciones": pendientes(tenant_id)}


class Decision(BaseModel):
    tenant_id: str
    tipo: str                       # aprobacion_descuento | proveedor_sin_confirmar
    referencia: str                 # lead_id (P1) | booking_id (P2)
    aprobada: bool
    proveedor_sustituto_id: str | None = None


@router.post("/owner/aprobaciones/responder")
def responder(decision: Decision, request: Request,
              x_internal_token: str = Header(default="")):
    _autorizar(x_internal_token)

    if decision.tipo == "aprobacion_descuento":
        graph = request.app.state.graph
        if runtime.interrupt_pendiente(graph, decision.tenant_id,
                                       decision.referencia) is None:
            raise HTTPException(404, "no hay aprobación pendiente para ese lead")
        resultado = runtime.responder_interrupt(
            graph, decision.tenant_id, decision.referencia,
            aprobado=decision.aprobada)
    elif decision.tipo == "proveedor_sin_confirmar":
        graph = request.app.state.graph_p2
        if runtime.interrupt_pendiente_p2(graph, decision.tenant_id,
                                          decision.referencia) is None:
            raise HTTPException(404, "no hay escalación pendiente para esa reserva")
        resultado = runtime.responder_interrupt_p2(
            graph, decision.tenant_id, decision.referencia,
            proveedor_sustituto_id=(decision.proveedor_sustituto_id
                                    if decision.aprobada else None))
    else:
        raise HTTPException(400, f"tipo desconocido: {decision.tipo!r}")

    try:
        from tools.aprobaciones import resolver
        resolver(decision.tenant_id, decision.tipo, decision.referencia,
                 decision.aprobada)
    except Exception as exc:
        log.warning("no se pudo marcar la aprobación resuelta: %s", exc)

    return {"status": "ok", "resultado": resultado.get("resultado")}
