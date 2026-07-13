"""Non-negotiable rule 1 (availability side): the EXCLUDE constraint is the
physical lock — no agent code path can double-book a space."""


import psycopg
import pytest

from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def espacio(admin, two_tenants):
    tenant_a, _ = two_tenants
    espacio_id = admin.execute(
        "insert into espacios (tenant_id, nombre, aforo_max) values (%s, 'Salón Test', 100) returning id",
        (tenant_a,),
    ).fetchone()[0]
    lead_id = admin.execute(
        "insert into leads (tenant_id, canal) values (%s, 'whatsapp') returning id",
        (tenant_a,),
    ).fetchone()[0]
    return {"tenant": tenant_a, "espacio": espacio_id, "lead": lead_id}


def _reservar(admin, ctx, inicio, fin, estado="confirmada"):
    return admin.execute(
        """insert into reservas (tenant_id, lead_id, espacio_id, inicio, fin, estado)
           values (%(t)s, %(l)s, %(e)s, %(i)s, %(f)s, %(s)s) returning id""",
        {"t": ctx["tenant"], "l": ctx["lead"], "e": ctx["espacio"],
         "i": inicio, "f": fin, "s": estado},
    ).fetchone()[0]


def test_solape_rechazado(admin, espacio):
    _reservar(admin, espacio, "2026-09-12 15:00-05", "2026-09-12 20:00-05")
    with pytest.raises(psycopg.errors.ExclusionViolation):
        _reservar(admin, espacio, "2026-09-12 18:00-05", "2026-09-12 23:00-05")


def test_hold_tambien_bloquea(admin, espacio):
    _reservar(admin, espacio, "2026-09-13 15:00-05", "2026-09-13 20:00-05", estado="hold")
    with pytest.raises(psycopg.errors.ExclusionViolation):
        _reservar(admin, espacio, "2026-09-13 16:00-05", "2026-09-13 18:00-05")


def test_rangos_adyacentes_permitidos(admin, espacio):
    _reservar(admin, espacio, "2026-09-14 10:00-05", "2026-09-14 14:00-05")
    _reservar(admin, espacio, "2026-09-14 14:00-05", "2026-09-14 18:00-05")  # no solapa


def test_cancelada_no_bloquea(admin, espacio):
    rid = _reservar(admin, espacio, "2026-09-15 15:00-05", "2026-09-15 20:00-05")
    admin.execute("update reservas set estado = 'cancelada' where id = %s", (rid,))
    _reservar(admin, espacio, "2026-09-15 16:00-05", "2026-09-15 19:00-05")


def test_liberacion_queda_auditada(admin, espacio):
    """Non-negotiable rule 5: every release is audited (trigger on reservas)."""
    rid = _reservar(admin, espacio, "2026-09-16 15:00-05", "2026-09-16 20:00-05", estado="hold")
    admin.execute("update reservas set estado = 'cancelada' where id = %s", (rid,))
    row = admin.execute(
        "select accion from eventos_auditoria where entidad = 'reserva' and entidad_id = %s",
        (rid,),
    ).fetchone()
    assert row is not None
    assert "hold -> cancelada" in row[0]
