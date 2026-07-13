"""Shared fixtures for the DB test suite.

Requires a migrated Postgres reachable at DATABASE_URL (make dev / CI service).
If the database is unreachable the DB tests are skipped with an explicit
message instead of failing, so pure-unit runs stay green on machines without
Docker.
"""

import os
import uuid

import psycopg
import pytest

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/aiveridia"
)


def _db_available() -> bool:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3):
            return True
    except psycopg.OperationalError:
        return False


requires_db = pytest.mark.skipif(
    not _db_available(),
    reason=f"Postgres no disponible en {DATABASE_URL} (levanta `make dev`)",
)


@pytest.fixture
def admin():
    """Superuser connection: bypasses RLS, used to arrange fixtures."""
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        yield conn


@pytest.fixture
def two_tenants(admin):
    """Two throwaway tenants, dropped (cascade) after the test."""
    ids = (str(uuid.uuid4()), str(uuid.uuid4()))
    for i, tid in enumerate(ids):
        admin.execute(
            "insert into tenants (id, nombre) values (%s, %s)",
            (tid, f"tenant-test-{i}"),
        )
    yield ids
    for tid in ids:
        admin.execute("delete from tenants where id = %s", (tid,))


def agent_cursor(conn: psycopg.Connection, tenant_id: str):
    """Apply the same context the runtime uses: RLS role + tenant GUC."""
    conn.execute("set role aiv_agent")
    conn.execute("select set_config('app.tenant_id', %s, false)", (tenant_id,))
    return conn


# ===========================================================================
# Fakes del grafo P1 (LLM + tools deterministas) — compartidos por los tests
# del grafo y los de la API de borde.
# ===========================================================================
from datetime import date, datetime, timedelta, timezone  # noqa: E402

from langchain_core.messages import AIMessage  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

import graphs.graph_comercial as gc  # noqa: E402
import tools.bookings as bookings  # noqa: E402
import tools.contracts as contracts  # noqa: E402
import tools.crm as crm  # noqa: E402
import tools.events as events  # noqa: E402
import tools.payments as payments  # noqa: E402
import tools.pricing as pricing  # noqa: E402
import tools.timers as timers  # noqa: E402
from graphs.graph_comercial import Calificacion, build_graph  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"
FECHA = date.today() + timedelta(days=30)
COMPLETA = Calificacion(fecha_evento=FECHA, aforo=40, tipo_evento="cumpleaños",
                        nombre_agasajado="Valentina")
INCOMPLETA = Calificacion(tipo_evento="cumpleaños")


class FakeLLM:
    def with_structured_output(self, schema):
        return self

    def invoke(self, mensajes):
        return AIMessage(content="[respuesta del agente]")


class FakeExtractor:
    def __init__(self, guion):
        self.guion = list(guion)

    def invoke(self, mensajes):
        return self.guion.pop(0) if len(self.guion) > 1 else self.guion[0]


@pytest.fixture(autouse=True)
def timers_null():
    """Backend de timers limpio por test (registra, no dispara)."""
    backend = timers.NullTimers()
    timers.configurar(backend)
    yield backend
    timers.configurar(timers.NullTimers())


@pytest.fixture
def entorno(monkeypatch, timers_null):
    """Grafo con MemorySaver + tools/LLM falsos. Devuelve helpers y registros."""
    registro = {"publicados": [], "nurturing": 0, "liberados": 0, "holds": 0}
    extractor = FakeExtractor([COMPLETA])

    def fake_get_llm(task):
        llm = FakeLLM()
        if task == "extraccion":
            llm.with_structured_output = lambda schema: extractor
        return llm

    monkeypatch.setattr(gc, "get_llm", fake_get_llm)
    monkeypatch.setattr(crm, "ensure_lead", lambda *a, **k: None)
    monkeypatch.setattr(crm, "actualizar_calificacion", lambda *a, **k: None)
    monkeypatch.setattr(crm, "marcar_cotizado", lambda *a, **k: None)
    monkeypatch.setattr(crm, "marcar_seguimiento", lambda *a, **k: None)
    monkeypatch.setattr(crm, "to_nurturing",
                        lambda *a, **k: registro.__setitem__("nurturing", registro["nurturing"] + 1))
    monkeypatch.setattr(bookings, "check_availability",
                        lambda tenant_id, fecha, aforo: {"libre": True,
                                                         "espacio_id": "e001",
                                                         "espacio_nombre": "Sala Kids"})
    monkeypatch.setattr(bookings, "nearest_free_dates",
                        lambda *a, **k: ["2026-08-15", "2026-08-22", "2026-08-29"])
    monkeypatch.setattr(bookings, "create_hold",
                        lambda *a, **k: (registro.__setitem__("holds", registro["holds"] + 1),
                                         "hold-001")[1])
    monkeypatch.setattr(bookings, "confirm_booking", lambda *a, **k: "rsv-001")
    monkeypatch.setattr(bookings, "release_hold",
                        lambda *a, **k: registro.__setitem__("liberados", registro["liberados"] + 1))
    monkeypatch.setattr(pricing, "cotizar",
                        lambda tenant_id, calificacion, descuento_solicitado=0.0: {
                            "paquete_id": "p002", "paquete_nombre": "Fiesta Clásica",
                            "incluye": ["local 5h"], "precio_lista": 2800.0,
                            "descuento_pct": descuento_solicitado,
                            "precio_final": round(2800 * (1 - descuento_solicitado / 100), 2),
                            "valida_hasta": datetime.now(timezone.utc) + timedelta(hours=48)})
    monkeypatch.setattr(pricing, "registrar_cotizacion", lambda *a, **k: "cot-001")
    monkeypatch.setattr(payments, "crear_link_separacion",
                        lambda t, lead, p: {"url": "https://pago.test/x", "pago_id": "pg1",
                                            "monto": round(p * 0.3, 2)})
    monkeypatch.setattr(payments, "marcar_pago_recibido", lambda *a, **k: None)
    monkeypatch.setattr(payments, "vencer_pago", lambda *a, **k: None)
    monkeypatch.setattr(contracts, "generar_contrato_pdf",
                        lambda t, b: f"contratos/contrato_{b}.md")
    monkeypatch.setattr(events, "publish",
                        lambda tipo, **d: registro["publicados"].append((tipo, d)))

    import rag.retriever as retriever
    monkeypatch.setattr(retriever, "contexto_para", lambda *a, **k: "")

    graph = build_graph(MemorySaver())
    return {"graph": graph, "registro": registro,
            "set_guion": lambda guion: setattr(extractor, "guion", list(guion)),
            "timers": timers_null}
