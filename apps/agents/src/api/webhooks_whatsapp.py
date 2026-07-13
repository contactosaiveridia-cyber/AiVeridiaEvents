"""WhatsApp Cloud API webhook (Meta).

GET  = verificación del endpoint (hub.challenge).
POST = mensajes entrantes. La lógica vive en api/deps.procesar_webhook_whatsapp
(compartida con el entrypoint AgentCore). Si algún mensaje falla, su dedup se
libera y se responde 500 para que Meta reintente; los procesados quedan
dedupeados (regla 4, at-least-once sin efectos dobles).
"""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from api.deps import procesar_webhook_whatsapp
from core.config import settings

router = APIRouter()


@router.get("/webhooks/whatsapp")
def verificar(hub_mode: str = Query("", alias="hub.mode"),
              hub_token: str = Query("", alias="hub.verify_token"),
              hub_challenge: str = Query("", alias="hub.challenge")):
    if hub_mode == "subscribe" and hub_token == settings.whatsapp_verify_token:
        return int(hub_challenge)
    raise HTTPException(403, "token de verificación inválido")


@router.post("/webhooks/whatsapp")
async def recibir(request: Request):
    body = await request.json()
    # threadpool: el procesamiento es síncrono (psycopg + LLM) y no debe
    # congelar el event loop mientras el modelo responde
    resultado = await run_in_threadpool(
        procesar_webhook_whatsapp, request.app.state.graph, body)
    if resultado["fallidos"]:
        return JSONResponse(resultado, status_code=500)  # Meta reintenta
    return resultado
