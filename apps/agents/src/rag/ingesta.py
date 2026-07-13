"""Per-tenant knowledge ingestion: FAQ, reglas del local, descripciones de
paquetes, políticas de reprogramación.

markdown -> chunking por secciones/párrafos -> embeddings (router) -> upsert.
Re-ingestar una fuente reemplaza sus chunks (delete + insert): idempotente.

CLI:  python -m rag.ingesta --tenant <uuid> --dir db/conocimiento/los_jazmines
"""

import argparse
import logging
from pathlib import Path

from core.db import tenant_connection
from rag.embeddings import a_pgvector, get_embedder

log = logging.getLogger("aiveridia.rag")

CHUNK_MAX_CHARS = 700


def trocear(texto: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """Split on blank lines, merging consecutive paragraphs up to max_chars.
    Headings (#) stick to the paragraph that follows them."""
    parrafos = [p.strip() for p in texto.split("\n\n") if p.strip()]
    chunks: list[str] = []
    actual = ""
    for p in parrafos:
        candidato = f"{actual}\n\n{p}".strip() if actual else p
        if len(candidato) <= max_chars:
            actual = candidato
        else:
            if actual:
                chunks.append(actual)
            actual = p
    if actual:
        chunks.append(actual)
    # un párrafo individual más largo que max_chars se parte duro
    finales: list[str] = []
    for c in chunks:
        while len(c) > max_chars:
            finales.append(c[:max_chars])
            c = c[max_chars:]
        finales.append(c)
    return [c for c in finales if c.strip()]


def ingest_texto(tenant_id: str, contenido: str, fuente: str,
                 embedder=None) -> int:
    embedder = embedder or get_embedder()
    chunks = trocear(contenido)
    vectores = embedder.embed(chunks)

    with tenant_connection(tenant_id) as conn:
        conn.execute("delete from conocimiento where fuente = %s", (fuente,))
        for chunk, vector in zip(chunks, vectores):
            conn.execute(
                """insert into conocimiento (tenant_id, contenido, embedding, fuente)
                   values (%s, %s, %s::vector, %s)""",
                (tenant_id, chunk, a_pgvector(vector), fuente),
            )
    log.info("ingestados %d chunks de %s para tenant %s", len(chunks), fuente, tenant_id)
    return len(chunks)


def ingest_directorio(tenant_id: str, directorio: str | Path, embedder=None) -> int:
    embedder = embedder or get_embedder()
    total = 0
    for archivo in sorted(Path(directorio).glob("*.md")):
        total += ingest_texto(tenant_id, archivo.read_text(encoding="utf-8"),
                              fuente=archivo.stem, embedder=embedder)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--dir", required=True)
    args = parser.parse_args()
    n = ingest_directorio(args.tenant, args.dir)
    print(f"{n} chunks ingestados")
