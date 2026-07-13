"""Tenant-scoped retriever over conocimiento (pgvector HNSW coseno).

Doble candado de aislamiento (regla 3):
  1. La conexión corre como aiv_agent con app.tenant_id -> RLS filtra filas.
  2. El WHERE explícito por tenant_id documenta la intención y protege ante
     una conexión mal configurada.
Un test de fuga cruzada (test_rag_aislamiento) demuestra que el tenant A jamás
recupera conocimiento del tenant B.
"""

import logging

from core.db import tenant_connection
from rag.embeddings import a_pgvector, get_embedder

log = logging.getLogger("aiveridia.rag")


def buscar(tenant_id: str, consulta: str, k: int = 4,
           embedder=None) -> list[dict]:
    embedder = embedder or get_embedder()
    vector = a_pgvector(embedder.embed([consulta])[0])
    with tenant_connection(tenant_id) as conn:
        filas = conn.execute(
            """select contenido, fuente,
                      1 - (embedding <=> %(v)s::vector) as score
                 from conocimiento
                where tenant_id = %(t)s and embedding is not null
                order by embedding <=> %(v)s::vector
                limit %(k)s""",
            {"v": vector, "t": tenant_id, "k": k},
        ).fetchall()
    return [dict(f) for f in filas]


def contexto_para(tenant_id: str, consulta: str, k: int = 4) -> str:
    """Formatted context block for the A1 prompt; '' when nothing applies or
    the knowledge base is unreachable (the funnel must never break by RAG)."""
    if not consulta:
        return ""
    try:
        resultados = buscar(tenant_id, consulta, k=k)
    except Exception as exc:  # sin DB / sin embedder: el agente sigue sin contexto
        log.warning("RAG no disponible (%s); se responde sin contexto", exc)
        return ""
    if not resultados:
        return ""
    piezas = [f"[{r['fuente']}] {r['contenido']}" for r in resultados]
    return ("Información oficial del salón para responder dudas "
            "(no inventes nada fuera de esto):\n" + "\n---\n".join(piezas))
