"""Lead lifecycle helpers (BPMN Task_Nurturing and estado transitions)."""

import json

from core.db import tenant_connection


def ensure_lead(tenant_id: str, lead_id: str, canal: str = "whatsapp",
                telefono: str | None = None) -> None:
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            """insert into leads (id, tenant_id, canal, telefono)
               values (%s, %s, %s, %s) on conflict (id) do nothing""",
            (lead_id, tenant_id, canal, telefono),
        )


def actualizar_calificacion(tenant_id: str, lead_id: str, calificacion: dict,
                            completa: bool) -> None:
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            """update leads set calificacion = %s,
                                estado = case when %s and estado = 'nuevo'
                                              then 'calificado' else estado end
                where id = %s""",
            (json.dumps(calificacion, default=str), completa, lead_id),
        )


def marcar_cotizado(tenant_id: str, lead_id: str) -> None:
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            "update leads set estado = 'cotizado' where id = %s", (lead_id,)
        )


def marcar_seguimiento(tenant_id: str, lead_id: str) -> None:
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            "update leads set estado = 'seguimiento' where id = %s", (lead_id,)
        )


def tenant_por_whatsapp(phone_number_id: str) -> str | None:
    """Resolve the tenant from the WhatsApp Business phone_number_id (edge,
    runs before any tenant context exists)."""
    from core.db import admin_connection
    with admin_connection() as conn:
        row = conn.execute(
            "select id from tenants where whatsapp_phone_id = %s", (phone_number_id,)
        ).fetchone()
    return str(row["id"]) if row else None


def lead_para_telefono(tenant_id: str, telefono: str) -> str:
    """Reuse the open funnel for this phone or create a fresh lead."""
    with tenant_connection(tenant_id) as conn:
        row = conn.execute(
            """select id from leads
                where telefono = %s
                  and estado not in ('convertido', 'perdido', 'nurturing')
                order by creado_en desc limit 1""",
            (telefono,),
        ).fetchone()
        if row:
            return str(row["id"])
        row = conn.execute(
            """insert into leads (tenant_id, canal, telefono)
               values (%s, 'whatsapp', %s) returning id""",
            (tenant_id, telefono),
        ).fetchone()
        return str(row["id"])


def to_nurturing(tenant_id: str, lead_id: str) -> None:
    """Task_Nurturing: fin 'no convertido' — el lead queda para largo plazo."""
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            "update leads set estado = 'nurturing' where id = %s", (lead_id,)
        )
