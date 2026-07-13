"""Post-event loop (BPMN A8/A9): NPS, métricas del dueño y campaña anual —
el moat de recurrencia."""

from datetime import datetime

from core.db import admin_connection, tenant_connection


def guardar_nps(tenant_id: str, booking_id: str, score: int,
                comentario: str | None = None) -> None:
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            """insert into nps_respuestas (tenant_id, reserva_id, score, comentario)
               values (%s, %s, %s, %s)""",
            (tenant_id, booking_id, score, comentario),
        )


def marcar_ejecutada(tenant_id: str, booking_id: str) -> None:
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            "update reservas set estado = 'ejecutada' where id = %s", (booking_id,)
        )


def refresh_metricas() -> None:
    """A9: refresca la vista del dashboard (pg_cron nocturno en prod; aquí
    también tras cada evento ejecutado para que el dueño vea datos frescos)."""
    with admin_connection() as conn:
        conn.execute("refresh materialized view metricas_tenant")


def crear_campania(tenant_id: str, booking_id: str, agasajado: str | None,
                   disparar_en: datetime) -> str:
    with tenant_connection(tenant_id) as conn:
        row = conn.execute(
            """insert into campanias_renovacion
                   (tenant_id, reserva_origen, agasajado, disparar_en)
               values (%s, %s, %s, %s) returning id""",
            (tenant_id, booking_id, agasajado, disparar_en),
        ).fetchone()
        return str(row["id"])


def marcar_campania_enviada(tenant_id: str, booking_id: str) -> None:
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            """update campanias_renovacion set estado = 'enviada'
                where reserva_origen = %s and estado = 'programada'""",
            (booking_id,),
        )
