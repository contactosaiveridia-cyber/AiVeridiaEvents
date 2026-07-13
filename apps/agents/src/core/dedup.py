"""Idempotency store for webhooks (rule 4).

`registrar_si_nuevo` is atomic: INSERT .. ON CONFLICT DO NOTHING — under
concurrent retries only one delivery wins. Memory backend for tests/dev
without Postgres.
"""

import json
import threading

from core.config import settings
from core.db import admin_connection


class PostgresDedup:
    def registrar_si_nuevo(self, fuente: str, evento_id: str,
                           tenant_id: str | None = None,
                           payload: dict | None = None) -> bool:
        """True si el evento es nuevo (procesar); False si es un reintento."""
        with admin_connection() as conn:
            row = conn.execute(
                """insert into webhook_eventos (fuente, evento_id, tenant_id, payload)
                   values (%s, %s, %s, %s)
                   on conflict (fuente, evento_id) do nothing
                   returning id""",
                (fuente, evento_id, tenant_id, json.dumps(payload or {}, default=str)),
            ).fetchone()
            return row is not None

    def eliminar(self, fuente: str, evento_id: str) -> None:
        """Compensación: si el procesamiento falló tras reclamar el evento, se
        libera para que el reintento del emisor lo reprocese (at-least-once)."""
        with admin_connection() as conn:
            conn.execute(
                "delete from webhook_eventos where fuente = %s and evento_id = %s",
                (fuente, evento_id),
            )


class MemoryDedup:
    def __init__(self) -> None:
        self._vistos: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def registrar_si_nuevo(self, fuente: str, evento_id: str,
                           tenant_id: str | None = None,
                           payload: dict | None = None) -> bool:
        with self._lock:
            clave = (fuente, evento_id)
            if clave in self._vistos:
                return False
            self._vistos.add(clave)
            return True

    def eliminar(self, fuente: str, evento_id: str) -> None:
        with self._lock:
            self._vistos.discard((fuente, evento_id))


_memoria = MemoryDedup()


def get_dedup():
    if settings.aiv_dedup == "memory":
        return _memoria
    return PostgresDedup()
