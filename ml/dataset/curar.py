"""Curation: WhatsApp exports -> multi-turn chat dataset (ShareGPT) for QLoRA.

Fuentes (ver arquitectura_mvp.md §2):
  1. Transcripciones reales etiquetadas por resultado; las conversaciones
     GANADORAS se sobre-muestrean (x2) — se aprende del vendedor que cierra.
  2. Cotizaciones históricas / objeciones (mismo formato, sesiones cortas).
  3. Sintético controlado (sintetico.py) validado por humano.

Uso:
  python curar.py --export chats/lead1.txt --etiqueta convertido \
                  --negocio "+51944123123" --out train.jsonl
"""

import argparse
import json
import re
from pathlib import Path

from anonimizar import anonimizar_texto

SYSTEM_PROMPT = (
    "Eres el asistente comercial por WhatsApp de un salón de eventos y fiestas "
    "infantiles en Perú. Tono cálido peruano, respuestas breves. Nunca fijas "
    "precios, descuentos ni disponibilidad: esos datos te los da el sistema."
)

# "12/05/25, 14:03 - +51 999 888 777: hola señito"
RE_LINEA = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{2,4}),?\s+(\d{1,2}:\d{2}(?:\s?[ap]\.?\s?m\.?)?)\s+-\s+"
    r"([^:]+):\s(.*)$")

OVERSAMPLE = {"convertido": 2, "perdido": 1}


def parse_whatsapp_export(texto: str, numero_negocio: str) -> list[tuple[str, str]]:
    """-> [(rol, mensaje)] con rol 'gpt' (negocio) o 'human' (cliente).
    Las líneas de continuación se pegan al mensaje anterior."""
    turnos: list[tuple[str, str]] = []
    for linea in texto.splitlines():
        m = RE_LINEA.match(linea.strip())
        if m:
            emisor, mensaje = m.group(3).strip(), m.group(4).strip()
            if "cifrado de extremo a extremo" in mensaje or "<Multimedia omitido>" in mensaje:
                continue
            rol = "gpt" if numero_negocio in emisor.replace(" ", "") else "human"
            turnos.append((rol, mensaje))
        elif turnos and linea.strip():
            rol, previo = turnos[-1]
            turnos[-1] = (rol, f"{previo}\n{linea.strip()}")

    # fusionar ráfagas del mismo emisor en un solo turno
    fusionados: list[tuple[str, str]] = []
    for rol, msg in turnos:
        if fusionados and fusionados[-1][0] == rol:
            fusionados[-1] = (rol, f"{fusionados[-1][1]}\n{msg}")
        else:
            fusionados.append((rol, msg))
    return fusionados


def a_sharegpt(turnos: list[tuple[str, str]], etiqueta: str) -> dict | None:
    """ShareGPT multi-turno; la conversación debe empezar por el cliente y
    tener al menos un intercambio completo."""
    while turnos and turnos[0][0] == "gpt":
        turnos = turnos[1:]
    if sum(1 for r, _ in turnos if r == "gpt") < 1:
        return None
    conversations = [{"from": "system", "value": SYSTEM_PROMPT}]
    conversations += [{"from": rol, "value": msg} for rol, msg in turnos]
    return {"conversations": conversations, "label": etiqueta}


def curar(archivos: list[tuple[Path, str]], numero_negocio: str,
          nombres_adultos: list[str] | None = None,
          nombres_ninos: list[str] | None = None) -> list[dict]:
    ejemplos: list[dict] = []
    for ruta, etiqueta in archivos:
        crudo = ruta.read_text(encoding="utf-8")
        # primero se parsea (los emisores identifican los roles) y recién
        # entonces se anonimiza el TEXTO de cada turno; los emisores se
        # descartan por completo, nunca llegan al dataset
        turnos = parse_whatsapp_export(crudo, numero_negocio)
        turnos = [(rol, anonimizar_texto(msg, nombres_adultos, nombres_ninos))
                  for rol, msg in turnos]
        ejemplo = a_sharegpt(turnos, etiqueta)
        if ejemplo:
            ejemplos.extend([ejemplo] * OVERSAMPLE.get(etiqueta, 1))
    return ejemplos


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", action="append", required=True)
    parser.add_argument("--etiqueta", action="append", required=True,
                        choices=["convertido", "perdido"])
    parser.add_argument("--negocio", required=True)
    parser.add_argument("--out", default="train.jsonl")
    args = parser.parse_args()

    pares = list(zip(map(Path, args.export), args.etiqueta))
    ejemplos = curar(pares, args.negocio.replace(" ", ""))
    with open(args.out, "w", encoding="utf-8") as fh:
        for e in ejemplos:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"{len(ejemplos)} ejemplos -> {args.out}")
