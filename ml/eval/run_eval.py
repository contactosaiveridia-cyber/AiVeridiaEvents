"""Corre la evaluación de regresión contra el modelo activo del router.

El target reproduce lo que hace A1 con cada conversación dorada:
  1. extraer la Calificacion (structured output),
  2. redactar la respuesta comercial,
  3. decidir escalamiento por descuento (código, no LLM).

  LANGSMITH_API_KEY=... python run_eval.py          # evalúa y sube resultados
"""

import re
import sys
from pathlib import Path

# reutiliza el código real del servicio (mismo router, mismos prompts)
sys.path.insert(0, str(Path(__file__).parents[2] / "apps" / "agents" / "src"))

from langsmith import evaluate  # noqa: E402

from evaluadores import ls_adherencia, ls_escalamiento, ls_extraccion  # noqa: E402

UMBRAL = 10.0
RE_DESCUENTO = re.compile(r"(\d{1,2}(?:\.\d+)?)\s?%")


def target(inputs: dict) -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage
    from graphs.graph_comercial import Calificacion
    from llm import prompts
    from llm.router import get_llm

    mensajes = [HumanMessage(m["texto"]) if m["role"] == "cliente"
                else SystemMessage(f"[respuesta previa del agente] {m['texto']}")
                for m in inputs["conversacion"]]

    extractor = get_llm("extraccion").with_structured_output(Calificacion)
    calificacion = extractor.invoke(
        [SystemMessage(prompts.EXTRACCION.format(hoy=inputs["hoy"])), *mensajes])

    respuesta = get_llm("conversacion").invoke(
        [SystemMessage(prompts.PERSONA_BASE.format(salon="Los Jazmines",
                                                   ciudad="Trujillo")), *mensajes])

    # escalamiento = código determinista sobre el descuento pedido
    texto_cliente = " ".join(m["texto"] for m in inputs["conversacion"]
                             if m["role"] == "cliente")
    pedido = max((float(m) for m in RE_DESCUENTO.findall(texto_cliente)),
                 default=0.0)
    return {"calificacion": calificacion.model_dump(mode="json"),
            "respuesta": respuesta.content,
            "escalo": pedido > UMBRAL}


if __name__ == "__main__":
    resultados = evaluate(
        target,
        data="aiveridia-events-regresion-v1",
        evaluators=[ls_extraccion, ls_adherencia, ls_escalamiento],
        experiment_prefix="aiveridia-events",
        max_concurrency=2,
    )
    print(resultados)
