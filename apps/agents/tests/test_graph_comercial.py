"""P1 graph funnel tests: routing, waits, interrupt de descuento y finales.

LLM y tools deterministas van con dobles (fixture `entorno` en conftest): aquí
se prueba el CONTRATO del BPMN (gateways, esperas, eventos de reanudación). La
capa de datos real ya está cubierta por test_rls_isolation/test_double_booking.
"""

import pytest

import graphs.graph_comercial as gc
import tools.bookings as bookings
from graphs import runtime
from tests.conftest import COMPLETA, FECHA, INCOMPLETA, TENANT


def _evento(entorno, lead, evento, texto=None, payload=None):
    return runtime.procesar_evento(entorno["graph"], TENANT, lead, evento,
                                   texto=texto, payload=payload)


def test_repregunta_cuando_faltan_datos(entorno):
    entorno["set_guion"]([INCOMPLETA, COMPLETA])
    r = _evento(entorno, "lead-a", "mensaje_cliente", "hola, quiero una fiesta")
    assert r["calificacion"].completa() is False
    assert r["messages"][-1].type == "ai"          # repregunta enviada
    assert r.get("cotizacion") is None             # y el grafo quedó esperando

    r = _evento(entorno, "lead-a", "mensaje_cliente",
                f"para el {FECHA}, 40 niños, cumpleaños")
    assert r["cotizacion"] is not None             # segundo turno completa y cotiza


def test_funnel_completo_hasta_confirmada(entorno):
    lead = "lead-funnel"
    r = _evento(entorno, lead, "mensaje_cliente", "quiero cotizar mi fiesta")
    assert r["fecha_disponible"] is True
    assert r["cotizacion"].precio_final == 2800.0  # precio de reglas, sin descuento

    r = _evento(entorno, lead, "cliente_acepta")
    assert r["estado_pago"] == gc.EstadoPago.PENDIENTE
    assert r["link_pago"] == "https://pago.test/x"
    assert entorno["registro"]["holds"] == 1       # la fecha quedó en hold

    r = _evento(entorno, lead, "pago_ok", payload={"pasarela_ref": "culqi-123"})
    assert r["resultado"] == "confirmada"
    assert r["booking_id"] == "rsv-001"
    assert r["contrato_url"].endswith("contrato_rsv-001.md")
    assert entorno["registro"]["publicados"] == [
        ("reserva.confirmada", {"tenant_id": TENANT, "booking_id": "rsv-001"})]


def test_fecha_ocupada_propone_alternativas(entorno, monkeypatch):
    monkeypatch.setattr(bookings, "check_availability",
                        lambda *a, **k: {"libre": False, "espacio_id": None,
                                         "espacio_nombre": None})
    r = _evento(entorno, "lead-ocupada", "mensaje_cliente", "quiero el sábado")
    assert r["fecha_disponible"] is False
    assert r["messages"][-1].type == "ai"          # alternativas enviadas
    assert r.get("cotizacion") is None             # espera Evt_Eleccion


def test_descuento_sobre_umbral_interrumpe_al_dueno(entorno):
    lead = "lead-desc"
    _evento(entorno, lead, "mensaje_cliente", "quiero 15% de descuento",
            payload={"descuento_solicitado": 15.0})

    pendiente = runtime.interrupt_pendiente(entorno["graph"], TENANT, lead)
    assert pendiente is not None                   # regla 2: freno humano
    assert pendiente["descuento_pct"] == 15.0

    r = runtime.responder_interrupt(entorno["graph"], TENANT, lead, aprobado=True)
    assert r["descuento_aprobado"] is True
    assert r["cotizacion"].descuento_pct == 15.0
    assert r["cotizacion"].precio_final == 2380.0


def test_descuento_rechazado_recotiza_sin_descuento(entorno):
    lead = "lead-rechazo"
    _evento(entorno, lead, "mensaje_cliente", "quiero 20% de descuento",
            payload={"descuento_solicitado": 20.0})
    assert runtime.interrupt_pendiente(entorno["graph"], TENANT, lead)

    r = runtime.responder_interrupt(entorno["graph"], TENANT, lead, aprobado=False)
    assert r["cotizacion"].descuento_pct == 0.0    # re-cotizado sin descuento
    assert r["cotizacion"].precio_final == 2800.0
    assert runtime.interrupt_pendiente(entorno["graph"], TENANT, lead) is None


def test_descuento_bajo_umbral_no_interrumpe(entorno):
    lead = "lead-desc-bajo"
    r = _evento(entorno, lead, "mensaje_cliente", "hay algún descuentito?",
                payload={"descuento_solicitado": 5.0})
    assert runtime.interrupt_pendiente(entorno["graph"], TENANT, lead) is None
    assert r["cotizacion"].descuento_pct == 5.0


def test_escalera_de_seguimiento_hasta_nurturing(entorno):
    lead = "lead-frio"
    _evento(entorno, lead, "mensaje_cliente", "cotízame porfa")

    r = _evento(entorno, lead, "timeout_cotizacion")   # 48 h sin respuesta
    assert r["n_seguimientos"] == 1
    assert r.get("resultado") is None

    r = _evento(entorno, lead, "timeout_final")        # 7 días sin respuesta
    assert r["resultado"] == "no_convertido"
    assert entorno["registro"]["nurturing"] == 1


def test_escalera_de_pago_hasta_liberar_fecha(entorno):
    lead = "lead-moroso"
    _evento(entorno, lead, "mensaje_cliente", "cotiza")
    _evento(entorno, lead, "cliente_acepta")

    r = _evento(entorno, lead, "timeout_pago")         # 24 h sin pago
    assert r["estado_pago"] == gc.EstadoPago.RECORDADO
    assert entorno["registro"]["liberados"] == 0       # regla 5: aún NO se libera

    r = _evento(entorno, lead, "timeout_pago_final")   # 48 h más sin pago
    assert r["resultado"] == "cancelada"
    assert r["estado_pago"] == gc.EstadoPago.VENCIDO
    assert entorno["registro"]["liberados"] == 1


def test_evento_desconocido_rechazado(entorno):
    with pytest.raises(ValueError):
        _evento(entorno, "lead-x", "evento_inventado")


def test_esperas_programan_y_eventos_cancelan_timers(entorno):
    """Cada nodo de espera programa su timeout one-shot; el evento competidor
    del gateway lo cancela al llegar (EventBridge Scheduler en prod)."""
    lead = "lead-timers"
    t = entorno["timers"]

    _evento(entorno, lead, "mensaje_cliente", "cotízame")
    assert [e for (_, quien, e, _) in t.programados if quien == lead] == ["timeout_cotizacion"]

    _evento(entorno, lead, "cliente_acepta")
    assert (TENANT, lead, "timeout_cotizacion") in t.cancelados
    assert [e for (_, quien, e, _) in t.programados if quien == lead][-1] == "timeout_pago"

    _evento(entorno, lead, "pago_ok")
    assert (TENANT, lead, "timeout_pago") in t.cancelados
