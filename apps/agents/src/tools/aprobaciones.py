"""Owner approval inbox: mirrors pending LangGraph interrupts into a queryable
table (RLS) so the dashboard can list them; resolving goes through the agents
API, which resumes the thread and marks the row."""

import json
import logging

from core.db import tenant_connection

log = logging.getLogger("aiveridia.aprobaciones")


def registrar_pendiente(tenant_id: str, tipo: str, referencia: str,
                        payload: dict) -> None:
    """Idempotente: el índice parcial único evita duplicar la misma espera."""
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            """insert into aprobaciones (tenant_id, tipo, referencia, payload)
               values (%s, %s, %s, %s)
               on conflict (tenant_id, tipo, referencia) where estado = 'pendiente'
               do nothing""",
            (tenant_id, tipo, referencia, json.dumps(payload, default=str)),
        )


def pendientes(tenant_id: str) -> list[dict]:
    with tenant_connection(tenant_id) as conn:
        filas = conn.execute(
            """select id, tipo, referencia, payload, creado_en
                 from aprobaciones where estado = 'pendiente'
                order by creado_en""").fetchall()
    return [dict(f) for f in filas]


def resolver(tenant_id: str, tipo: str, referencia: str, aprobada: bool) -> None:
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            """update aprobaciones
                  set estado = %s, resuelto_en = now()
                where tipo = %s and referencia = %s and estado = 'pendiente'""",
            ("aprobada" if aprobada else "rechazada", tipo, referencia),
        )
