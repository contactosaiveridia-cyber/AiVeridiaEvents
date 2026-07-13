"""Internal resume endpoint: target of EventBridge Scheduler (prod, via the
resume Lambda) and of the APScheduler dev backend. Fires timeout_* events (P1),
operational timers (P2, incl. 'timeout_proveedor:{id}') and the P2 chain start.
Protected by shared token (Secrets Manager in prod)."""

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from api.deps import rutear_evento
from core.config import settings
from graphs.graph_comercial import ENTRYPOINTS_RESUME
from graphs.graph_operativo import ENTRYPOINTS_RESUME_P2

router = APIRouter()


class ResumeBody(BaseModel):
    tenant_id: str
    evento: str
    lead_id: str | None = None       # P1
    booking_id: str | None = None    # P2
    telefono: str | None = None


@router.post("/internal/resume")
def resume(body: ResumeBody, request: Request,
           x_internal_token: str = Header(default="")):
    if x_internal_token != settings.aiv_internal_token:
        raise HTTPException(401, "token interno inválido")

    base = body.evento.partition(":")[0]
    if base in ENTRYPOINTS_RESUME_P2:
        if not body.booking_id:
            raise HTTPException(400, "evento P2 requiere booking_id")
        clave = body.booking_id
    elif base in ENTRYPOINTS_RESUME:
        if not body.lead_id:
            raise HTTPException(400, "evento P1 requiere lead_id")
        clave = body.lead_id
    else:
        raise HTTPException(400, f"evento desconocido: {body.evento!r}")

    resultado = rutear_evento(request.app.state, body.tenant_id, clave,
                              body.evento, telefono=body.telefono)
    return {"status": "ok", "evento": body.evento,
            "resultado": resultado.get("resultado")}
