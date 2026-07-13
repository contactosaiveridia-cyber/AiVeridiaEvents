"""Regresión de los hallazgos de la revisión: cada test reproduce el escenario
de fallo reportado y verifica el fix."""

import pytest

import api.deps as deps
from graphs import runtime
from tests.conftest import TENANT
from tools.timers import _timer_id


# ── Hallazgo 4: aceptación con negación/aplazamiento ────────────────────────
ESTADO_ESPERANDO = {"cotizacion": object(), "estado_pago": None}


@pytest.mark.parametrize("texto", [
    "sí, separa la fecha porfa", "acepto", "dale, lo tomo", "ok confirmo"])
def test_aceptaciones_claras(texto):
    assert deps.es_aceptacion(texto, ESTADO_ESPERANDO) is True


@pytest.mark.parametrize("texto", [
    "no, no acepto ese precio", "ok, pero primero lo consulto",
    "sí, pero todavía no", "dale, luego te aviso", "jamás aceptaría eso",
    "ya te aviso", "déjame pensarlo y te confirmo"])
def test_negaciones_y_aplazamientos_no_aceptan(texto):
    assert deps.es_aceptacion(texto, ESTADO_ESPERANDO) is False


def test_sin_cotizacion_vigente_nunca_acepta():
    assert deps.es_aceptacion("sí acepto", {"cotizacion": None}) is False
    assert deps.es_aceptacion(
        "sí acepto", {"cotizacion": object(), "estado_pago": "pendiente"}) is False


# ── Hallazgo 3: nombre de schedule EventBridge ──────────────────────────────
def test_timer_id_cabe_en_eventbridge_y_distingue_eventos():
    tenant = "11111111-1111-1111-1111-111111111111"
    lead = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ids = {_timer_id(tenant, lead, e) for e in
           ("timeout_cotizacion", "timeout_final", "timeout_pago",
            "timeout_pago_final", "timeout_proveedor:prov-1",
            "timeout_proveedor:prov-2")}
    assert len(ids) == 6                        # sin colisiones tras truncar
    for tid in ids:
        assert len(tid) <= 64
        assert ":" not in tid                   # patrón EventBridge [0-9a-zA-Z-_.]
    # determinista: programar y cancelar generan el mismo nombre
    assert _timer_id(tenant, lead, "timeout_pago") == _timer_id(tenant, lead, "timeout_pago")


# ── Hallazgo 2: entrega con fallback al teléfono del estado ─────────────────
def test_pago_y_timeouts_entregan_al_telefono_del_estado(entorno, monkeypatch):
    """pago_ok y los timeouts no traen telefono: debe usarse el guardado en el
    estado del thread desde el primer mensaje de WhatsApp."""
    enviados = []

    class Canal:
        def send_text(self, t, to, texto): enviados.append((to, texto))
        def send_media(self, *a, **k): pass

    monkeypatch.setattr(deps, "get_channel", lambda *a, **k: Canal())
    graph = entorno["graph"]

    deps.despachar_evento(graph, TENANT, "lead-tel", "mensaje_cliente",
                          texto="cotízame", telefono="51988877766")
    deps.despachar_evento(graph, TENANT, "lead-tel", "cliente_acepta")   # sin telefono
    deps.despachar_evento(graph, TENANT, "lead-tel", "pago_ok")          # sin telefono

    estado = graph.get_state(runtime.thread_config(TENANT, "lead-tel")).values
    assert estado["telefono"] == "51988877766"      # persistido en el thread
    assert estado["resultado"] == "confirmada"
    assert [to for to, _ in enviados] == ["51988877766"] * 3   # las 3 entregas salieron


# ── Hallazgo 1: dedup compensable (el reintento reprocesa tras un fallo) ────
def test_pago_fallido_libera_dedup_y_reintento_procesa(entorno, monkeypatch):
    import core.dedup as dedup_mod
    from core.config import settings

    monkeypatch.setattr(settings, "aiv_dedup", "memory")
    monkeypatch.setattr(dedup_mod, "_memoria", dedup_mod.MemoryDedup())

    cuerpo = (b'{"pasarela": "culqi", "transaccion_id": "txn-r1", '
              b'"metadata": {"tenant_id": "%s", "lead_id": "lead-retry"}}'
              % TENANT.encode())

    llamadas = {"n": 0}
    original = deps.despachar_evento

    def falla_primero(*args, **kwargs):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise RuntimeError("DB caída")
        return original(*args, **kwargs)

    monkeypatch.setattr(deps, "despachar_evento", falla_primero)

    with pytest.raises(RuntimeError):               # primer intento: falla
        deps.procesar_webhook_pagos(entorno["graph"], cuerpo, "")

    # el reintento de la pasarela NO debe ver 'duplicado': debe procesar
    deps.despachar_evento(entorno["graph"], TENANT, "lead-retry",
                          "mensaje_cliente", texto="cotiza")   # prepara cotización
    deps.despachar_evento(entorno["graph"], TENANT, "lead-retry", "cliente_acepta")
    respuesta, codigo = deps.procesar_webhook_pagos(entorno["graph"], cuerpo, "")
    assert codigo == 200
    assert respuesta["status"] == "procesado"       # no 'duplicado'


def test_whatsapp_fallido_libera_dedup_y_reporta(entorno, monkeypatch):
    import core.dedup as dedup_mod
    import tools.crm as crm
    from core.config import settings

    monkeypatch.setattr(settings, "aiv_dedup", "memory")
    monkeypatch.setattr(dedup_mod, "_memoria", dedup_mod.MemoryDedup())
    monkeypatch.setattr(crm, "tenant_por_whatsapp", lambda pid: TENANT)
    monkeypatch.setattr(crm, "lead_para_telefono", lambda t, tel: f"lead-{tel}")

    body = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "510000000001"},
        "messages": [{"id": "wamid.f1", "from": "519", "type": "text",
                      "text": {"body": "hola"}}]}}]}]}

    falla = {"activa": True}
    original = deps.despachar_evento

    def tal_vez_falla(*args, **kwargs):
        if falla["activa"]:
            raise RuntimeError("boom")
        return original(*args, **kwargs)

    monkeypatch.setattr(deps, "despachar_evento", tal_vez_falla)
    r = deps.procesar_webhook_whatsapp(entorno["graph"], body)
    assert r["fallidos"] == 1                       # el router responderá 500

    falla["activa"] = False
    r = deps.procesar_webhook_whatsapp(entorno["graph"], body)
    assert r["procesados"] == 1                     # reintento: NO es duplicado
