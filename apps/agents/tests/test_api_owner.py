"""F7: bandeja de aprobaciones del dueño — el interrupt se refleja en la
bandeja, se lista por API y responderlo reanuda el thread correcto."""

import pytest
from fastapi.testclient import TestClient

import tools.aprobaciones as aprobaciones
from core.config import settings
from graphs import runtime
from tests.conftest import TENANT

TOKEN = {"X-Internal-Token": settings.aiv_internal_token}


class BandejaMemoria:
    def __init__(self):
        self.filas: list[dict] = []

    def registrar_pendiente(self, tenant_id, tipo, referencia, payload):
        if any(f["referencia"] == referencia and f["estado"] == "pendiente"
               for f in self.filas):
            return
        self.filas.append({"id": f"apr-{len(self.filas)}", "tipo": tipo,
                           "referencia": referencia, "payload": payload,
                           "estado": "pendiente", "creado_en": "2026-07-12"})

    def pendientes(self, tenant_id):
        return [f for f in self.filas if f["estado"] == "pendiente"]

    def resolver(self, tenant_id, tipo, referencia, aprobada):
        for f in self.filas:
            if f["referencia"] == referencia and f["estado"] == "pendiente":
                f["estado"] = "aprobada" if aprobada else "rechazada"


@pytest.fixture
def owner_client(entorno, monkeypatch):
    from api.main import app

    monkeypatch.setenv("AIV_CHECKPOINTER", "memory")
    bandeja = BandejaMemoria()
    monkeypatch.setattr(aprobaciones, "registrar_pendiente", bandeja.registrar_pendiente)
    monkeypatch.setattr(aprobaciones, "pendientes", bandeja.pendientes)
    monkeypatch.setattr(aprobaciones, "resolver", bandeja.resolver)

    with TestClient(app) as c:
        c.bandeja = bandeja
        yield c


def _generar_interrupt(client, lead="lead-owner"):
    from api.deps import despachar_evento
    despachar_evento(client.app.state.graph, TENANT, lead, "mensaje_cliente",
                     texto="quiero 18% de descuento",
                     payload={"descuento_solicitado": 18.0})
    return lead


def test_interrupt_aparece_en_bandeja_y_por_api(owner_client):
    lead = _generar_interrupt(owner_client)

    r = owner_client.get("/owner/aprobaciones",
                         params={"tenant_id": TENANT}, headers=TOKEN)
    assert r.status_code == 200
    [fila] = r.json()["aprobaciones"]
    assert fila["tipo"] == "aprobacion_descuento"
    assert fila["referencia"] == lead
    assert fila["payload"]["descuento_pct"] == 18.0


def test_responder_aprueba_y_reanuda_el_thread(owner_client):
    lead = _generar_interrupt(owner_client)

    r = owner_client.post("/owner/aprobaciones/responder", headers=TOKEN,
                          json={"tenant_id": TENANT, "referencia": lead,
                                "tipo": "aprobacion_descuento", "aprobada": True})
    assert r.status_code == 200

    estado = owner_client.app.state.graph.get_state(
        runtime.thread_config(TENANT, lead)).values
    assert estado["descuento_aprobado"] is True
    assert estado["cotizacion"].descuento_pct == 18.0     # cotización enviada
    assert owner_client.bandeja.filas[0]["estado"] == "aprobada"


def test_responder_rechaza_recotiza_sin_descuento(owner_client):
    lead = _generar_interrupt(owner_client, "lead-owner-no")
    owner_client.post("/owner/aprobaciones/responder", headers=TOKEN,
                      json={"tenant_id": TENANT, "referencia": lead,
                            "tipo": "aprobacion_descuento", "aprobada": False})
    estado = owner_client.app.state.graph.get_state(
        runtime.thread_config(TENANT, lead)).values
    assert estado["cotizacion"].descuento_pct == 0.0
    assert owner_client.bandeja.filas[-1]["estado"] == "rechazada"


def test_sin_pendiente_devuelve_404_y_sin_token_401(owner_client):
    r = owner_client.post("/owner/aprobaciones/responder", headers=TOKEN,
                          json={"tenant_id": TENANT, "referencia": "lead-nada",
                                "tipo": "aprobacion_descuento", "aprobada": True})
    assert r.status_code == 404

    r = owner_client.get("/owner/aprobaciones", params={"tenant_id": TENANT})
    assert r.status_code == 401
