"""Regla 3 / criterio de aceptación: fuga de RAG entre tenants = NEGATIVO.

Se ingesta conocimiento para dos tenants con un embedder determinista y se
demuestra que el retriever del tenant A jamás devuelve contenido del B aunque
la consulta sea semánticamente idéntica al documento del B.
"""

import pytest

from tests.conftest import requires_db

pytestmark = requires_db


class EmbedderDeterminista:
    """Mismo vector para todo: si el aislamiento fallara, el doc del otro
    tenant saldría primero con score perfecto."""

    def embed(self, textos):
        from rag.embeddings import DIM
        return [[1.0] + [0.0] * (DIM - 1) for _ in textos]


@pytest.fixture
def conocimiento_cruzado(two_tenants):
    from rag.ingesta import ingest_texto

    tenant_a, tenant_b = two_tenants
    emb = EmbedderDeterminista()
    ingest_texto(tenant_a, "El salón A permite llevar tu propia torta.",
                 fuente="faq", embedder=emb)
    ingest_texto(tenant_b, "SECRETO-B: precios internos del salón B.",
                 fuente="faq", embedder=emb)
    return tenant_a, tenant_b, emb


def test_fuga_rag_entre_tenants_negativa(conocimiento_cruzado):
    from rag.retriever import buscar

    tenant_a, tenant_b, emb = conocimiento_cruzado
    resultados_a = buscar(tenant_a, "precios internos", k=10, embedder=emb)

    assert resultados_a, "el tenant A debe ver su propio conocimiento"
    contenido_a = " ".join(r["contenido"] for r in resultados_a)
    assert "SECRETO-B" not in contenido_a
    assert all("torta" in r["contenido"] for r in resultados_a)

    resultados_b = buscar(tenant_b, "torta", k=10, embedder=emb)
    assert all("SECRETO-B" in r["contenido"] for r in resultados_b)


def test_reingesta_reemplaza_fuente(conocimiento_cruzado):
    from rag.ingesta import ingest_texto
    from rag.retriever import buscar

    tenant_a, _, emb = conocimiento_cruzado
    ingest_texto(tenant_a, "Versión 2 de la FAQ del salón A.",
                 fuente="faq", embedder=emb)
    resultados = buscar(tenant_a, "faq", k=10, embedder=emb)
    assert len(resultados) == 1                    # sin duplicados
    assert "Versión 2" in resultados[0]["contenido"]
