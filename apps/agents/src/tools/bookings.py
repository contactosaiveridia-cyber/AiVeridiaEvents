"""Deterministic availability & booking (BPMN Task_Disponibilidad, Task_LinkPago
hold, Task_Registrar, Task_Liberar).

Availability = SQL over reservas; the EXCLUDE constraint is the physical lock.
The agent never "decides" a date is free — the database does.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.db import tenant_connection

TZ_DEFAULT = "America/Lima"
# MVP: un evento bloquea el día completo del espacio (los salones de Trujillo
# hacen 1 evento/día por espacio). Slots por hora llegarán con datos reales.


def _rango_dia(fecha: date, tz: str = TZ_DEFAULT) -> tuple[datetime, datetime]:
    zona = ZoneInfo(tz)
    inicio = datetime.combine(fecha, time.min, tzinfo=zona)
    return inicio, inicio + timedelta(days=1)


def _espacio_libre(conn, fecha: date, aforo: int) -> dict | None:
    """Smallest space that fits and is free that date (maximize utilization)."""
    inicio, fin = _rango_dia(fecha)
    return conn.execute(
        """select e.id, e.nombre, e.aforo_max
             from espacios e
            where e.aforo_max >= %(aforo)s
              and not exists (
                    select 1 from reservas r
                     where r.espacio_id = e.id
                       and r.estado in ('hold', 'confirmada')
                       and tstzrange(r.inicio, r.fin) && tstzrange(%(ini)s, %(fin)s))
            order by e.aforo_max
            limit 1""",
        {"aforo": aforo, "ini": inicio, "fin": fin},
    ).fetchone()


def check_availability(tenant_id: str, fecha: date, aforo: int) -> dict:
    """Returns {"libre": bool, "espacio_id": ..., "espacio_nombre": ...}."""
    with tenant_connection(tenant_id) as conn:
        espacio = _espacio_libre(conn, fecha, aforo)
    if espacio is None:
        return {"libre": False, "espacio_id": None, "espacio_nombre": None}
    return {
        "libre": True,
        "espacio_id": str(espacio["id"]),
        "espacio_nombre": espacio["nombre"],
    }


def nearest_free_dates(tenant_id: str, fecha: date, aforo: int, n: int = 3) -> list[str]:
    """Next n free dates scanning forward; same weekday first (cumples son sábado)."""
    candidatas: list[date] = []
    mismo_dow = [fecha + timedelta(weeks=k) for k in (1, 2, 3, 4)]
    dias_cercanos = [fecha + timedelta(days=d) for d in range(1, 22)]
    vistas = set()
    for c in mismo_dow + dias_cercanos:
        if c not in vistas:
            vistas.add(c)
            candidatas.append(c)

    libres: list[str] = []
    with tenant_connection(tenant_id) as conn:
        for c in candidatas:
            if _espacio_libre(conn, c, aforo) is not None:
                libres.append(c.isoformat())
                if len(libres) == n:
                    break
    return libres


def create_hold(tenant_id: str, lead_id: str, espacio_id: str, fecha: date,
                cotizacion_id: str | None = None) -> str | None:
    """Pre-reserva (estado hold). Returns None if the EXCLUDE constraint fires
    (race with another lead): the caller must offer alternatives."""
    import psycopg

    inicio, fin = _rango_dia(fecha)
    try:
        with tenant_connection(tenant_id) as conn:
            row = conn.execute(
                """insert into reservas (tenant_id, lead_id, espacio_id,
                                         cotizacion_id, inicio, fin, estado)
                   values (%s, %s, %s, %s, %s, %s, 'hold') returning id""",
                (tenant_id, lead_id, espacio_id, cotizacion_id, inicio, fin),
            ).fetchone()
            return str(row["id"])
    except psycopg.errors.ExclusionViolation:
        return None


def confirm_booking(tenant_id: str, lead_id: str) -> str:
    """Task_Registrar: promote the lead's hold to confirmada, mark lead won."""
    with tenant_connection(tenant_id) as conn:
        row = conn.execute(
            """update reservas set estado = 'confirmada'
                where lead_id = %s and estado = 'hold' returning id""",
            (lead_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"lead {lead_id}: no hay hold que confirmar")
        conn.execute(
            "update leads set estado = 'convertido' where id = %s", (lead_id,)
        )
        return str(row["id"])


def datos_reserva(tenant_id: str, booking_id: str) -> dict:
    """Contexto de la reserva confirmada que necesita el grafo operativo P2."""
    with tenant_connection(tenant_id) as conn:
        row = conn.execute(
            """select r.id, r.inicio::date as fecha_evento, r.lead_id,
                      l.telefono, l.nombre as cliente,
                      l.calificacion->>'nombre_agasajado' as agasajado,
                      c.precio_final,
                      p.nombre as paquete,
                      t.nombre as salon,
                      t.branding->>'ciudad' as ciudad,
                      t.branding->>'link_resena' as link_resena
                 from reservas r
                 join leads l on l.id = r.lead_id
                 join tenants t on t.id = r.tenant_id
                 left join cotizaciones c on c.id = r.cotizacion_id
                 left join paquetes p on p.id = c.paquete_id
                where r.id = %s""",
            (booking_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"reserva {booking_id} no encontrada")
    return {k: (str(v) if k in ("id", "lead_id") else v) for k, v in row.items()}


def release_hold(tenant_id: str, lead_id: str) -> None:
    """Task_Liberar: only after the full reminder ladder (rule 5). The estado
    transition is audited by the trg_audit_reserva_estado trigger."""
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            """update reservas set estado = 'cancelada'
                where lead_id = %s and estado = 'hold'""",
            (lead_id,),
        )
        conn.execute("update leads set estado = 'perdido' where id = %s", (lead_id,))
