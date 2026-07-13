"""Embedding router — same pattern as get_llm(): one decision point.

prod: Bedrock Titan Embed Text v2 (1024 dims, normalizado).
dev:  Ollama nomic-embed-text (768 dims) con zero-padding a 1024.

La columna `conocimiento.embedding` es vector(1024) (índice HNSW coseno).
El zero-padding NO altera la similitud coseno entre vectores del mismo
modelo (no cambia ni el producto punto ni las normas), así que dev y prod
comparten esquema; en cada entorno se re-ingesta con su propio embedder.
"""

import json
from typing import Protocol

from core.config import settings

DIM = 1024


class Embedder(Protocol):
    def embed(self, textos: list[str]) -> list[list[float]]: ...


def _pad(vector: list[float]) -> list[float]:
    if len(vector) > DIM:
        raise ValueError(f"embedding de {len(vector)} dims no cabe en vector({DIM})")
    return vector + [0.0] * (DIM - len(vector))


class OllamaEmbedder:
    def __init__(self) -> None:
        from langchain_ollama import OllamaEmbeddings

        self._emb = OllamaEmbeddings(model="nomic-embed-text",
                                     base_url=settings.ollama_base_url)

    def embed(self, textos: list[str]) -> list[list[float]]:
        return [_pad(v) for v in self._emb.embed_documents(textos)]


class TitanEmbedder:
    def __init__(self) -> None:
        import boto3

        self._client = boto3.client("bedrock-runtime")

    def embed(self, textos: list[str]) -> list[list[float]]:
        salida = []
        for texto in textos:
            r = self._client.invoke_model(
                modelId="amazon.titan-embed-text-v2:0",
                body=json.dumps({"inputText": texto, "dimensions": DIM,
                                 "normalize": True}),
            )
            salida.append(json.loads(r["body"].read())["embedding"])
        return salida


def get_embedder() -> Embedder:
    if settings.aiv_env == "prod":
        return TitanEmbedder()
    return OllamaEmbedder()


def a_pgvector(vector: list[float]) -> str:
    """Literal aceptado por pgvector: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.8f}" for x in vector) + "]"
