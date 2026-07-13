"""F3 edge tests: idempotencia de webhooks (regla 4), firma de pasarela,
verificación de Meta, resume interno y entrega por canal."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

import core.dedup as dedup_mod
import tools.crm as crm
from core.config import settings
from graphs import runtime
from tests.conftest import TENANT

PHONE_ID = "510000000001"
TELEFONO = "51999888777"
LEAD = f"lead-{TELEFONO}"


def _payload_meta(msg_id: str, texto: str) -> dict:
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": PHONE_ID},
        "messages": [{"id": msg_id, "from": TELEFONO,
                      "type": "text", "text": {"body": texto}}],
    }}]}]}


class CanalRecorder:
    def __init__(self):
        self.enviados = []

    def send_text(self, tenant_id, to, text):
        self.enviados.append((tenant_id, to, text))

    def send_media(self, *a, **k):
        pass


@pytest.fixture
def cliente(entorno, monkeypatch):
    """App real (lifespan con MemorySaver) + fakes del grafo + canal grabador."""
    import api.deps as deps
    from api.main import app

    monkeypatch.setenv("AIV_CHECKPOINTER", "memory")
    monkeypatch.setattr(dedup_mod, "_memoria", dedup_mod.MemoryDedup())
    monkeypatch.setattr(settings, "aiv_dedup", "memory")
    monkeypatch.setattr(crm, "tenant_por_whatsapp",
                        lambda pid: TENANT if pid == PHONE_ID else None)
    monkeypatch.setattr(crm, "lead_para_telefono", lambda t, tel: f"lead-{tel}")

    canal = CanalRecorder()
    monkeypatch.setattr(deps, "get_channel", lambda *a, **k: canal)

    with TestClient(app) as c:
        c.canal = canal
        c.graph = app.state.graph
        yield c


def _estado(cliente, lead=LEAD):
    return cliente.graph.get_state(runtime.thread_config(TENANT, lead)).values


# ── WhatsApp ────────────────────────────────────────────────────────────────
def test_verificacion_meta(cliente):
    r = cliente.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": settings.whatsapp_verify_token,
        "hub.challenge": "4242"})
    assert r.status_code == 200 and r.json() == 4242

    r = cliente.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": "incorrecto",
        "hub.challenge": "1"})
    assert r.status_code == 403


def test_mensaje_entrante_cotiza_y_responde(cliente):
    r = cliente.post("/webhooks/whatsapp", json=_payload_meta("wamid.1", "quiero una fiesta"))
    assert r.status_code == 200
    assert r.json()["procesados"] == 1
    assert _estado(cliente)["cotizacion"] is not None
    assert len(cliente.canal.enviados) == 1        # respuesta entregada al cliente
    assert cliente.canal.enviados[0][1] == TELEFONO


def test_webhook_duplicado_no_genera_efectos_dobles(cliente):
    payload = _payload_meta("wamid.dup", "cotízame porfa")
    cliente.post("/webhooks/whatsapp", json=payload)
    n_mensajes = len(_estado(cliente)["messages"])

    r = cliente.post("/webhooks/whatsapp", json=payload)   # reintento de Meta
    assert r.status_code == 200                            # 200 para frenar reintentos
    assert r.json()["procesados"] == 0
    assert r.json()["duplicados"] == 1
    assert len(_estado(cliente)["messages"]) == n_mensajes
    assert len(cliente.canal.enviados) == 1


def test_aceptacion_detectada_dispara_link_de_pago(cliente):
    cliente.post("/webhooks/whatsapp", json=_payload_meta("wamid.a1", "cotiza mi evento"))
    cliente.post("/webhooks/whatsapp",
                 json=_payload_meta("wamid.a2", "sí, separa la fecha porfa"))
    estado = _estado(cliente)
    assert estado["estado_pago"] is not None               # cliente_acepta -> link
    assert estado["link_pago"] == "https://pago.test/x"


def test_phone_id_desconocido_se_ignora(cliente):
    payload = _payload_meta("wamid.x", "hola")
    payload["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "000"
    r = cliente.post("/webhooks/whatsapp", json=payload)
    assert r.status_code == 200 and r.json()["procesados"] == 0


# ── Pagos ───────────────────────────────────────────────────────────────────
def _pago_body(transaccion="txn-1", lead=LEAD):
    return json.dumps({"pasarela": "culqi", "transaccion_id": transaccion,
                       "pago_id": "pg1", "monto": 840.0,
                       "metadata": {"tenant_id": TENANT, "lead_id": lead}})


def _firma(cuerpo: str, secreto: str) -> str:
    return hmac.new(secreto.encode(), cuerpo.encode(), hashlib.sha256).hexdigest()


def test_pago_firma_invalida_rechazado(cliente, monkeypatch):
    monkeypatch.setattr(settings, "culqi_webhook_secret", "secreto-culqi")
    r = cliente.post("/webhooks/pagos", content=_pago_body(),
                     headers={"X-Aiv-Signature": "firma-falsa"})
    assert r.status_code == 401


def test_pago_ok_confirma_reserva_y_es_idempotente(cliente, entorno, monkeypatch):
    monkeypatch.setattr(settings, "culqi_webhook_secret", "secreto-culqi")
    cliente.post("/webhooks/whatsapp", json=_payload_meta("wamid.p1", "cotiza"))
    cliente.post("/webhooks/whatsapp", json=_payload_meta("wamid.p2", "sí acepto"))

    cuerpo = _pago_body("txn-777")
    headers = {"X-Aiv-Signature": _firma(cuerpo, "secreto-culqi"),
               "Content-Type": "application/json"}
    r = cliente.post("/webhooks/pagos", content=cuerpo, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "procesado"
    assert _estado(cliente)["resultado"] == "confirmada"
    assert len(entorno["registro"]["publicados"]) == 1     # reserva.confirmada 1 vez

    r = cliente.post("/webhooks/pagos", content=cuerpo, headers=headers)  # retry
    assert r.json()["status"] == "duplicado"
    assert len(entorno["registro"]["publicados"]) == 1     # sin efectos dobles


def test_pago_pasarela_desconocida(cliente):
    r = cliente.post("/webhooks/pagos",
                     content=json.dumps({"pasarela": "paypal", "transaccion_id": "1"}))
    assert r.status_code == 400


# ── Resume interno (Scheduler) ──────────────────────────────────────────────
def test_resume_requiere_token(cliente):
    r = cliente.post("/internal/resume",
                     json={"tenant_id": TENANT, "lead_id": LEAD,
                           "evento": "timeout_cotizacion"})
    assert r.status_code == 401


def test_resume_dispara_timeout(cliente):
    cliente.post("/webhooks/whatsapp", json=_payload_meta("wamid.t1", "cotiza"))
    r = cliente.post("/internal/resume",
                     json={"tenant_id": TENANT, "lead_id": LEAD,
                           "evento": "timeout_cotizacion", "telefono": TELEFONO},
                     headers={"X-Internal-Token": settings.aiv_internal_token})
    assert r.status_code == 200
    assert _estado(cliente)["n_seguimientos"] == 1
    assert cliente.canal.enviados[-1][2] == "[respuesta del agente]"  # seguimiento entregado

    r = cliente.post("/internal/resume",
                     json={"tenant_id": TENANT, "lead_id": LEAD, "evento": "inventado"},
                     headers={"X-Internal-Token": settings.aiv_internal_token})
    assert r.status_code == 400
