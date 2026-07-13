"""Checkpointer factory.

Postgres (langgraph.checkpoint.postgres) in dev/prod: the graph pauses at wait
nodes and any process can resume the thread later. MemorySaver only for tests
and the CLI simulator without a database (AIV_CHECKPOINTER=memory).
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager

from core.config import settings


@contextmanager
def open_checkpointer() -> Iterator:
    if os.getenv("AIV_CHECKPOINTER", "").lower() == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        yield MemorySaver()
        return

    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(settings.database_url) as saver:
        saver.setup()
        yield saver
