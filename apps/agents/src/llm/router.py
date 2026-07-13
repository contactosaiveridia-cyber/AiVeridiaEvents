"""Model router — "the LLM changes, the chain does not".

Single decision point for every model in the system:

  task "conversacion"/"extraccion":
    prod -> Bedrock CMI (AIVERIDIA_EVENTS_MODEL_ARN). While the fine-tuned
            model does not exist yet, degrade cleanly to base Llama 3.1 8B
            on Bedrock so the whole system is testable end-to-end.
    dev  -> Ollama. Preferred model aiveridia-events:8b; degrades to the
            first locally available fallback (llama3.1:8b, llama3.2, ...).
  task "razonamiento" (complex negotiations, claims):
    Gemini 2.5 Flash when GEMINI_API_KEY is set; otherwise degrades to the
    conversational model so dev environments keep working offline.
"""

import os
from functools import lru_cache
from typing import Literal

import httpx

from core.config import settings

Task = Literal["conversacion", "extraccion", "razonamiento"]

BEDROCK_BASE_FALLBACK = "meta.llama3-1-8b-instruct-v1:0"
OLLAMA_FALLBACKS = ("llama3.1:8b", "llama3.2", "llama3")


@lru_cache(maxsize=1)
def _resolve_ollama_model() -> str:
    """Preferred fine-tuned model if pulled; otherwise first available fallback."""
    preferred = settings.ollama_model
    try:
        tags = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=3).json()
        disponibles = {m["name"] for m in tags.get("models", [])}
        disponibles |= {n.split(":")[0] for n in disponibles}
    except Exception:
        return preferred  # server unreachable: fail later with a clear error
    for candidato in (preferred, *OLLAMA_FALLBACKS):
        if candidato in disponibles:
            return candidato
    return preferred


def get_llm(task: Task):
    """El LLM cambia, la chain no: un solo punto de decisión de modelo."""
    env = settings.aiv_env

    if task in ("conversacion", "extraccion"):
        temperature = 0.3 if task == "conversacion" else 0.0
        if env == "prod":
            from langchain_aws import ChatBedrockConverse

            model = settings.aiveridia_events_model_arn or BEDROCK_BASE_FALLBACK
            return ChatBedrockConverse(model=model, temperature=temperature)
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=_resolve_ollama_model(),
            base_url=settings.ollama_base_url,
            temperature=temperature,
        )

    # razonamiento complejo
    if os.getenv("GEMINI_API_KEY"):
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2)
    return get_llm("conversacion")
