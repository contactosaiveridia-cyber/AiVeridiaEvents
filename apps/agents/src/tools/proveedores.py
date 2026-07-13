"""Provider orchestration (BPMN A5, multi-instance).

Deterministic selection: best confiabilidad per required rubro. Each order gets
its own 48 h boundary timer (timeout_proveedor:{proveedor_id}); escalation to
the owner happens per unconfirmed instance.
"""

from core.db import tenant_connection

RUBROS_BASE = ["torta", "decoracion", "animacion"]
RUBROS_PREMIUM = RUBROS_BASE + ["catering", "fotografia"]


def crear_ordenes(tenant_id: str, booking_id: str, premium: bool = False) -> list[dict]:
    rubros = RUBROS_PREMIUM if premium else RUBROS_BASE
    ordenes: list[dict] = []
    with tenant_connection(tenant_id) as conn:
        for rubro in rubros:
            prov = conn.execute(
                """select id, nombre, rubro, telefono from proveedores
                    where rubro = %s order by confiabilidad desc nulls last limit 1""",
                (rubro,),
            ).fetchone()
            if prov is None:
                continue
            orden = conn.execute(
                """insert into ordenes_proveedor
                       (tenant_id, reserva_id, proveedor_id, detalle)
                   values (%s, %s, %s, %s) returning id""",
                (tenant_id, booking_id, prov["id"],
                 f'{{"rubro": "{rubro}"}}'),
            ).fetchone()
            ordenes.append({"orden_id": str(orden["id"]),
                            "proveedor_id": str(prov["id"]),
                            "nombre": prov["nombre"], "rubro": prov["rubro"],
                            "telefono": prov["telefono"]})
    return ordenes


def marcar_confirmado(tenant_id: str, booking_id: str, proveedor_id: str) -> None:
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            """update ordenes_proveedor
                  set estado = 'confirmado', confirmado_en = now()
                where reserva_id = %s and proveedor_id = %s""",
            (booking_id, proveedor_id),
        )


def marcar_escalado(tenant_id: str, booking_id: str, proveedor_id: str) -> None:
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            """update ordenes_proveedor set estado = 'escalado'
                where reserva_id = %s and proveedor_id = %s""",
            (booking_id, proveedor_id),
        )


def marcar_sustituido(tenant_id: str, booking_id: str, proveedor_id: str,
                      sustituto_id: str | None) -> None:
    """El dueño resolvió la escalación: la orden original queda sustituida y,
    si eligió reemplazo, se crea la orden nueva ya confirmada por él."""
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            """update ordenes_proveedor set estado = 'sustituido'
                where reserva_id = %s and proveedor_id = %s""",
            (booking_id, proveedor_id),
        )
        if sustituto_id:
            conn.execute(
                """insert into ordenes_proveedor
                       (tenant_id, reserva_id, proveedor_id, detalle, estado, confirmado_en)
                   select %s, %s, %s, detalle, 'confirmado', now()
                     from ordenes_proveedor
                    where reserva_id = %s and proveedor_id = %s limit 1""",
                (tenant_id, booking_id, sustituto_id, booking_id, proveedor_id),
            )
