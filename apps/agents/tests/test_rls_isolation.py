"""Non-negotiable rule 3: tenant A must never see or touch tenant B's data."""


import psycopg
import pytest

from tests.conftest import DATABASE_URL, agent_cursor, requires_db

pytestmark = requires_db


def _insert_lead(admin, tenant_id: str, nombre: str) -> str:
    row = admin.execute(
        "insert into leads (tenant_id, canal, nombre) values (%s, 'whatsapp', %s) returning id",
        (tenant_id, nombre),
    ).fetchone()
    return str(row[0])


def test_select_solo_ve_su_tenant(admin, two_tenants):
    tenant_a, tenant_b = two_tenants
    _insert_lead(admin, tenant_a, "lead-A")
    _insert_lead(admin, tenant_b, "lead-B")

    with psycopg.connect(DATABASE_URL) as conn:
        agent_cursor(conn, tenant_a)
        nombres = [r[0] for r in conn.execute("select nombre from leads").fetchall()]
        assert "lead-A" in nombres
        assert "lead-B" not in nombres


def test_insert_cruzado_bloqueado(admin, two_tenants):
    tenant_a, tenant_b = two_tenants
    with psycopg.connect(DATABASE_URL) as conn:
        agent_cursor(conn, tenant_a)
        with pytest.raises(psycopg.errors.Error) as exc:
            conn.execute(
                "insert into leads (tenant_id, canal, nombre) values (%s, 'whatsapp', 'intruso')",
                (tenant_b,),
            )
        assert "row-level security" in str(exc.value)


def test_update_cruzado_no_afecta_filas(admin, two_tenants):
    tenant_a, tenant_b = two_tenants
    lead_b = _insert_lead(admin, tenant_b, "lead-B")

    with psycopg.connect(DATABASE_URL) as conn:
        agent_cursor(conn, tenant_a)
        cur = conn.execute(
            "update leads set nombre = 'hackeado' where id = %s", (lead_b,)
        )
        assert cur.rowcount == 0

    row = admin.execute("select nombre from leads where id = %s", (lead_b,)).fetchone()
    assert row[0] == "lead-B"


def test_tenants_solo_se_ve_a_si_mismo(two_tenants):
    tenant_a, tenant_b = two_tenants
    with psycopg.connect(DATABASE_URL) as conn:
        agent_cursor(conn, tenant_a)
        rows = conn.execute("select id from tenants").fetchall()
        assert [str(r[0]) for r in rows] == [tenant_a]


def test_sin_contexto_no_ve_nada(admin, two_tenants):
    tenant_a, _ = two_tenants
    _insert_lead(admin, tenant_a, "lead-A")
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("set role aiv_agent")  # sin app.tenant_id
        assert conn.execute("select count(*) from leads").fetchone()[0] == 0


def test_conocimiento_rag_aislado(admin, two_tenants):
    """Anticipo del test de fuga RAG (F4): la tabla conocimiento ya es estanca."""
    tenant_a, tenant_b = two_tenants
    admin.execute(
        "insert into conocimiento (tenant_id, contenido, fuente) values (%s, 'secreto B', 'faq')",
        (tenant_b,),
    )
    with psycopg.connect(DATABASE_URL) as conn:
        agent_cursor(conn, tenant_a)
        assert conn.execute("select count(*) from conocimiento").fetchone()[0] == 0
