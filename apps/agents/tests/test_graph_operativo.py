"""P2 graph tests: multi-instancia de proveedores con escalación al dueño,
cobranza, checklist D-7, NPS, métricas y campaña anual. Tools con dobles;
se prueba el contrato del BPMN P2 y el encadenamiento P1 -> P2."""

from datetime import date, timedelta

import pytest
from langgraph.checkpoint.memory import MemorySaver

import graphs.graph_operativo as go
import tools.bookings as bookings
import tools.channels as channels
import tools.cobranza as cobranza
import tools.events as events
import tools.fidelizacion as fidelizacion
import tools.proveedores as proveedores
from graphs import runtime
from graphs.graph_operativo import build_graph_operativo
from tests.conftest import TENANT, FakeLLM

BOOKING = "rsv-001"
FECHA = (date.today() + timedelta(days=90)).isoformat()

PROVS = [
    {"orden_id": "o1", "proveedor_id": "p-torta", "nombre": "Tortas Dulce Sueño",
     "rubro": "torta", "telefono": "+519001"},
    {"orden_id": "o2", "proveedor_id": "p-deco", "nombre": "Decoraciones Fantasía",
     "rubro": "decoracion", "telefono": "+519002"},
    {"orden_id": "o3", "proveedor_id": "p-anim", "nombre": "Animaciones Happy Kids",
     "rubro": "animacion", "telefono": "+519003"},
]


class Canal:
    def __init__(self): self.enviados = []
    def send_text(self, t, to, texto): self.enviados.append((to, texto))
    def send_media(self, *a, **k): pass


@pytest.fixture
def p2(monkeypatch, timers_null):
    reg = {"confirmados": [], "escalados": [], "sustituidos": [],
           "nps": [], "campanias": [], "ejecutadas": 0, "refrescos": 0,
           "saldo": 1960.0}
    canal = Canal()

    monkeypatch.setattr(go, "get_llm", lambda task: FakeLLM())
    monkeypatch.setattr(channels, "get_channel", lambda *a, **k: canal)
    monkeypatch.setattr(bookings, "datos_reserva",
                        lambda t, b: {"id": b, "lead_id": "lead-1",
                                      "telefono": "51999", "cliente": "María",
                                      "agasajado": "Valentina",
                                      "fecha_evento": FECHA,
                                      "precio_final": 2800.0,
                                      "paquete": "Fiesta Clásica"})
    monkeypatch.setattr(proveedores, "crear_ordenes", lambda t, b, premium=False: PROVS)
    monkeypatch.setattr(proveedores, "marcar_confirmado",
                        lambda t, b, p: reg["confirmados"].append(p))
    monkeypatch.setattr(proveedores, "marcar_escalado",
                        lambda t, b, p: reg["escalados"].append(p))
    monkeypatch.setattr(proveedores, "marcar_sustituido",
                        lambda t, b, p, s: reg["sustituidos"].append((p, s)))
    monkeypatch.setattr(cobranza, "generar_cronograma",
                        lambda t, b: {"cuotas": [{"monto": 980.0}, {"monto": 980.0}],
                                      "saldo": 1960.0, "fecha_evento": FECHA})
    monkeypatch.setattr(cobranza, "registrar_pago_cuota",
                        lambda t, b, ref, medio="yape", monto=None:
                            reg.__setitem__("saldo", reg["saldo"] - 980.0) or reg["saldo"])
    monkeypatch.setattr(cobranza, "saldo_pendiente", lambda t, b: reg["saldo"])
    monkeypatch.setattr(fidelizacion, "guardar_nps",
                        lambda t, b, s, c=None: reg["nps"].append((s, c)))
    monkeypatch.setattr(fidelizacion, "marcar_ejecutada",
                        lambda t, b: reg.__setitem__("ejecutadas", reg["ejecutadas"] + 1))
    monkeypatch.setattr(fidelizacion, "refresh_metricas",
                        lambda: reg.__setitem__("refrescos", reg["refrescos"] + 1))
    monkeypatch.setattr(fidelizacion, "crear_campania",
                        lambda t, b, a, en: reg["campanias"].append((a, en)) or "camp-1")
    monkeypatch.setattr(fidelizacion, "marcar_campania_enviada", lambda t, b: None)

    import rag.retriever as retriever
    monkeypatch.setattr(retriever, "contexto_para", lambda *a, **k: "")

    graph = build_graph_operativo(MemorySaver())
    return {"graph": graph, "reg": reg, "canal": canal, "timers": timers_null}


def _evento(p2, evento, payload=None):
    return runtime.procesar_evento_p2(p2["graph"], TENANT, BOOKING, evento, payload)


def _timers_de(p2):
    return [e for (_, clave, e, _) in p2["timers"].programados if clave == BOOKING]


def test_inicio_paraleliza_proveedores_y_cronograma(p2):
    r = _evento(p2, "reserva_confirmada")

    assert r["ordenes"] == {p["proveedor_id"]: "notificado" for p in PROVS}
    assert len(p2["canal"].enviados) == 3              # una orden por proveedor
    assert "torta" in p2["canal"].enviados[0][1]
    assert r["cronograma"]["saldo"] == 1960.0
    assert r["saldo_pendiente"] == 1960.0
    assert r["telefono"] == "51999" and r["agasajado"] == "Valentina"

    timers = _timers_de(p2)
    assert sorted(t for t in timers if t.startswith("timeout_proveedor")) == [
        "timeout_proveedor:p-anim", "timeout_proveedor:p-deco",
        "timeout_proveedor:p-torta"]
    assert "checkpoint_d7" in timers                   # GW_Join vía timer D-7


def test_confirmacion_de_proveedor_cancela_su_boundary_timer(p2):
    _evento(p2, "reserva_confirmada")
    r = _evento(p2, "proveedor_confirma", {"proveedor_id": "p-torta"})

    assert r["ordenes"]["p-torta"] == "confirmado"
    assert r["ordenes"]["p-deco"] == "notificado"      # las demás instancias siguen
    assert p2["reg"]["confirmados"] == ["p-torta"]
    assert (TENANT, BOOKING, "timeout_proveedor:p-torta") in p2["timers"].cancelados


def test_escalacion_interrumpe_al_dueno_y_gestiona_sustituto(p2):
    _evento(p2, "reserva_confirmada")
    _evento(p2, "timeout_proveedor", {"proveedor_id": "p-deco"})

    pendiente = runtime.interrupt_pendiente_p2(p2["graph"], TENANT, BOOKING)
    assert pendiente["tipo"] == "proveedor_sin_confirmar"
    assert pendiente["proveedor_id"] == "p-deco"
    assert p2["reg"]["escalados"] == ["p-deco"]

    r = runtime.responder_interrupt_p2(p2["graph"], TENANT, BOOKING,
                                       proveedor_sustituto_id="p-deco-2")
    assert r["ordenes"]["p-deco"] == "sustituido"
    assert p2["reg"]["sustituidos"] == [("p-deco", "p-deco-2")]


def test_ciclo_completo_hasta_campania_anual(p2):
    _evento(p2, "reserva_confirmada")
    for prov in PROVS:
        _evento(p2, "proveedor_confirma", {"proveedor_id": prov["proveedor_id"]})

    r = _evento(p2, "pago_cuota", {"pago_ref": "yape-1"})
    assert r["saldo_pendiente"] == 980.0               # cuota 1 conciliada

    r = _evento(p2, "checkpoint_d7")                   # D-7 con saldo pendiente
    assert r["saldo_pendiente"] == 980.0
    assert len(r["messages"]) == 2                     # aviso de saldo + checklist
    assert "dia_evento" in _timers_de(p2)

    r = _evento(p2, "dia_evento")                      # montaje
    assert "post_evento" in _timers_de(p2)

    r = _evento(p2, "post_evento")                     # NPS +1 día
    assert p2["reg"]["ejecutadas"] == 1

    r = _evento(p2, "nps_respuesta", {"nps_score": 9,
                                      "nps_comentario": "todo lindo"})
    assert p2["reg"]["nps"] == [(9, "todo lindo")]
    assert p2["reg"]["refrescos"] == 1                 # métricas A9 actualizadas
    assert p2["reg"]["campanias"][0][0] == "Valentina"
    assert "campania_renovacion" in _timers_de(p2)     # +10 meses

    r = _evento(p2, "campania_renovacion")
    assert r["resultado"] == "ciclo_completado"        # End_Ciclo


def test_checkpoint_d7_sin_saldo_no_avisa_deuda(p2):
    _evento(p2, "reserva_confirmada")
    p2["reg"]["saldo"] = 0.0
    r = _evento(p2, "checkpoint_d7")
    assert len(r["messages"]) == 1                     # solo checklist


def test_encadenamiento_reserva_confirmada_arranca_p2(p2, monkeypatch):
    """End_Confirmada (P1) publica reserva.confirmada; el suscriptor arranca P2."""
    monkeypatch.setattr(events, "_subscribers", {})
    events.subscribe("reserva.confirmada",
                     lambda tenant_id, booking_id: runtime.procesar_evento_p2(
                         p2["graph"], tenant_id, booking_id, "reserva_confirmada"))

    events.publish("reserva.confirmada", tenant_id=TENANT, booking_id=BOOKING)

    estado = p2["graph"].get_state(
        runtime.thread_config_p2(TENANT, BOOKING)).values
    assert estado["ordenes"]                           # P2 inició y notificó


def test_evento_p2_desconocido_rechazado(p2):
    with pytest.raises(ValueError):
        _evento(p2, "evento_inventado")
