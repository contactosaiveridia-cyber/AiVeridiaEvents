"""Sube las 50 conversaciones doradas como dataset de LangSmith.

  LANGSMITH_API_KEY=... python crear_dataset_langsmith.py
"""

import json
from pathlib import Path

from langsmith import Client

NOMBRE = "aiveridia-events-regresion-v1"


def main() -> None:
    client = Client()
    if client.has_dataset(dataset_name=NOMBRE):
        print(f"dataset {NOMBRE} ya existe; bórralo para regenerarlo")
        return
    dataset = client.create_dataset(
        dataset_name=NOMBRE,
        description="50 conversaciones doradas: extracción de calificación, "
                    "adherencia 100% a precios de reglas, escalamiento de "
                    "descuentos y tono peruano.")

    ruta = Path(__file__).parent / "dataset_regresion.jsonl"
    doradas = [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines()]
    client.create_examples(
        dataset_id=dataset.id,
        inputs=[{"conversacion": d["conversacion"], "hoy": d["hoy"],
                 "escenario": d["escenario"]} for d in doradas],
        outputs=[d["esperado"] for d in doradas],
    )
    print(f"{len(doradas)} ejemplos subidos a {NOMBRE}")


if __name__ == "__main__":
    main()
