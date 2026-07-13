"""Payment gateway webhook (Culqi / MercadoPago).

Contrato normalizado del payload (los adaptadores por pasarela real mapean a
esto; el link de separación se crea con metadata tenant_id/lead_id):

    {"pasarela": "culqi" | "mercadopago",
     "transaccion_id": "...", "pago_id": "...", "monto": 840.0,
     "metadata": {"tenant_id": "...", "lead_id": "..."}}

Firma HMAC-SHA256 del cuerpo crudo en X-Aiv-Signature. La lógica vive en
api/deps.procesar_webhook_pagos (compartida con AgentCore): dedup por
transaccion_id con compensación — si el despacho falla, el evento se libera y
el reintento de la pasarela lo reprocesa en vez de perder el pago.
"""

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from api.deps import procesar_webhook_pagos

router = APIRouter()


@router.post("/webhooks/pagos")
async def recibir(request: Request,
                  x_aiv_signature: str = Header(default="")):
    cuerpo = await request.body()
    respuesta, codigo = await run_in_threadpool(
        procesar_webhook_pagos, request.app.state.graph, cuerpo, x_aiv_signature)
    return JSONResponse(respuesta, status_code=codigo)
