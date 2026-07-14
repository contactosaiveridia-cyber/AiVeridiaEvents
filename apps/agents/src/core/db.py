"""Tenant-scoped database access.

Every business query runs inside `tenant_connection(tenant_id)`:
  1. SET LOCAL ROLE aiv_agent      -> subject to RLS (NOSUPERUSER, NOBYPASSRLS)
  2. set_config('app.tenant_id')   -> current_tenant() resolves to this tenant

The transaction is the isolation boundary: role and tenant GUC are reset when
it ends, so pooled connections never leak tenant context. Connections come
from a process-wide psycopg_pool (a single WhatsApp turn touches the DB 4+
times; fresh TCP+auth per query would dominate latency and exhaust
max_connections under bursts).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import psycopg
from psycopg.rows import dict_row

from core.config import settings


def _make_pool(url: str):
    from psycopg_pool import ConnectionPool

    return ConnectionPool(
        url, min_size=0, max_size=10, timeout=10, open=True,
        kwargs={"row_factory": dict_row},
    )


@lru_cache(maxsize=1)
def _admin_pool():
    return _make_pool(settings.database_url)


@lru_cache(maxsize=1)
def _runtime_pool():
    return _make_pool(settings.runtime_url)


@contextmanager
def tenant_connection(tenant_id: str) -> Iterator[psycopg.Connection]:
    with _runtime_pool().connection() as conn:
        with conn.transaction():
            conn.execute("set local role aiv_agent")
            conn.execute(
                "select set_config('app.tenant_id', %s, true)", (tenant_id,)
            )
            yield conn


@contextmanager
def admin_connection() -> Iterator[psycopg.Connection]:
    """Unrestricted connection (migrations, seeds, edge dedup, maintenance)."""
    with _admin_pool().connection() as conn:
        yield conn
