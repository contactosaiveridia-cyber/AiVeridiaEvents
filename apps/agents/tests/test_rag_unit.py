"""RAG unit tests (sin base de datos): chunking, padding y embedder local."""

import math

import httpx
import pytest

from core.config import settings
from rag.embeddings import DIM, OllamaEmbedder, _pad, a_pgvector, get_embedder
from rag.ingesta import CHUNK_MAX_CHARS, trocear


def _coseno(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb)


def test_trocear_respeta_tamano_y_contenido():
    texto = "\n\n".join(f"## Sección {i}\n\nContenido del párrafo {i} " * 3
                        for i in range(12))
    chunks = trocear(texto)
    assert chunks
    assert all(0 < len(c) <= CHUNK_MAX_CHARS for c in chunks)
    assert "Sección 11" in "".join(chunks)          # nada se pierde


def test_trocear_parte_parrafos_gigantes():
    chunks = trocear("x" * (CHUNK_MAX_CHARS * 3 + 10))
    assert len(chunks) == 4
    assert all(len(c) <= CHUNK_MAX_CHARS for c in chunks)


def test_padding_preserva_coseno():
    import random
    rng = random.Random(42)
    a = [rng.uniform(-1, 1) for _ in range(768)]
    b = [rng.uniform(-1, 1) for _ in range(768)]
    assert math.isclose(_coseno(a, b), _coseno(_pad(a), _pad(b)), abs_tol=1e-12)
    assert len(_pad(a)) == DIM


def test_pad_rechaza_vectores_mas_grandes():
    with pytest.raises(ValueError):
        _pad([0.0] * (DIM + 1))


def test_a_pgvector_formato():
    assert a_pgvector([1.0, -0.5]) == "[1.00000000,-0.50000000]"


def test_router_usa_ollama_en_dev():
    assert settings.aiv_env == "dev"
    assert isinstance(get_embedder(), OllamaEmbedder)


def _ollama_disponible() -> bool:
    try:
        modelos = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=3).json()
        return any(m["name"].startswith("nomic-embed-text")
                   for m in modelos.get("models", []))
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_disponible(),
                    reason="Ollama/nomic-embed-text no disponible")
def test_embeddings_reales_ordenan_por_relevancia():
    emb = OllamaEmbedder()
    consulta, relevante, irrelevante = emb.embed([
        "¿el paquete incluye torta?",
        "La Fiesta Clásica incluye torta para 30 personas.",
        "Contamos con estacionamiento privado para 15 autos.",
    ])
    assert len(consulta) == DIM
    assert _coseno(consulta, relevante) > _coseno(consulta, irrelevante)
