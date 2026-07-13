"""FastAPI edge service around the P1 (comercial) and P2 (operativo) graphs.

Lifespan: opens the checkpointer (Postgres; memory with AIV_CHECKPOINTER=memory),
compiles both graphs, configures the timer backend (APScheduler in dev mimics
EventBridge Scheduler) and chains P1 -> P2 subscribing to reserva.confirmada
(in prod the EventBridge rule + resume Lambda does this, see infra/main.tf §4).
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api import internal, owner, webhooks_pagos, webhooks_whatsapp
from core.config import settings

logging.basicConfig(level=logging.INFO, format="%(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from api.deps import despachar_evento_p2, rutear_evento
    from graphs.checkpointer import open_checkpointer
    from graphs.graph_comercial import build_graph
    from graphs.graph_operativo import build_graph_operativo
    from tools import events, timers

    with open_checkpointer() as saver:
        app.state.graph = build_graph(saver)
        app.state.graph_p2 = build_graph_operativo(saver)

        # Encadenamiento P1 -> P2 (End_Confirmada es un message end event)
        events.subscribe(
            "reserva.confirmada",
            lambda tenant_id, booking_id: despachar_evento_p2(
                app.state.graph_p2, tenant_id, booking_id, "reserva_confirmada"))

        backend = None
        if settings.aiv_timers == "apscheduler":
            backend = timers.APSchedulerTimers(
                disparar=lambda t, clave, e: rutear_evento(app.state, t, clave, e))
            timers.configurar(backend)
        elif settings.aiv_timers == "eventbridge":
            timers.configurar(timers.EventBridgeTimers())
        else:
            timers.configurar(timers.NullTimers())

        try:
            yield
        finally:
            if isinstance(backend, timers.APSchedulerTimers):
                backend.shutdown()


app = FastAPI(title="aiVeridia Events — agents", version="0.5.0", lifespan=lifespan)
app.include_router(webhooks_whatsapp.router)
app.include_router(webhooks_pagos.router)
app.include_router(internal.router)
app.include_router(owner.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "aiveridia-agents", "env": settings.aiv_env}
