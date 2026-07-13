"""The Los Jazmines seed must satisfy the F1 contract: 3 espacios, 4 paquetes,
season/day price rules and 10 proveedores."""

import pytest

from tests.conftest import requires_db

pytestmark = requires_db

LOS_JAZMINES = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def jazmines(admin):
    row = admin.execute(
        "select id from tenants where id = %s", (LOS_JAZMINES,)
    ).fetchone()
    if row is None:
        pytest.skip("seed de Los Jazmines no cargado (make db-seed)")
    return LOS_JAZMINES


def _count(admin, table, tenant):
    return admin.execute(
        f"select count(*) from {table} where tenant_id = %s", (tenant,)
    ).fetchone()[0]


def test_seed_completo(admin, jazmines):
    assert _count(admin, "espacios", jazmines) == 3
    assert _count(admin, "paquetes", jazmines) == 4
    assert _count(admin, "proveedores", jazmines) == 10
    assert _count(admin, "reglas_precio", jazmines) >= 5


def test_reglas_cubren_temporada_y_dia(admin, jazmines):
    tipos = {
        r[0]
        for r in admin.execute(
            "select distinct tipo from reglas_precio where tenant_id = %s", (jazmines,)
        ).fetchall()
    }
    assert {"temporada", "dia_semana"} <= tipos
