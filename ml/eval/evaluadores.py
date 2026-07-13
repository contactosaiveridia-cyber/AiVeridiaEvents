"""Evaluadores del dataset de regresión (LangSmith).

1. extraccion_correcta : la calificación extraída coincide con el gold.
2. adherencia_precios  : la respuesta solo menciona montos permitidos (100%
                         obligatorio — regla innegociable 1). Cualquier S/ que
                         no venga del motor de reglas es un fallo.
3. tono (LLM-as-judge) : rúbrica de español peruano cálido-comercial.
"""

import re

RE_MONTO = re.compile(r"S/\.?\s?([\d.,]+)")

RUBRICA_TONO = """Evalúa la respuesta de un asistente de ventas de un salón de
fiestas infantiles en Perú. Puntúa de 0.0 a 1.0 según:
- Español peruano natural, cálido y cercano (no neutro corporativo). [0.4]
- Breve, apta para WhatsApp (sin párrafos largos ni formato de correo). [0.3]
- Empuja la venta con suavidad (pregunta de cierre o siguiente paso). [0.3]
Responde SOLO el número."""


def montos_en(texto: str) -> list[float]:
    montos = []
    for crudo in RE_MONTO.findall(texto):
        limpio = crudo.rstrip(".,")
        # normaliza "3,864.00" y "3864,00"
        if "," in limpio and "." in limpio:
            limpio = limpio.replace(",", "")
        elif "," in limpio:
            limpio = limpio.replace(",", ".")
        try:
            montos.append(round(float(limpio), 2))
        except ValueError:
            continue
    return montos


def adherencia_precios(respuesta: str, montos_permitidos: list[float]) -> bool:
    """True si TODOS los montos S/ de la respuesta están en la lista permitida
    (una respuesta sin montos siempre es adherente)."""
    permitidos = {round(m, 2) for m in montos_permitidos}
    return all(m in permitidos for m in montos_en(respuesta))


def extraccion_correcta(pred: dict, gold: dict | None) -> bool:
    if gold is None:
        return True
    if str(pred.get("fecha_evento") or "") != gold["fecha_evento"]:
        return False
    if int(pred.get("aforo") or 0) != gold["aforo"]:
        return False
    tipo_pred = (pred.get("tipo_evento") or "").lower()
    return gold["tipo_evento"].split()[0].lower() in tipo_pred


def evaluar_tono(llm, respuesta: str) -> float:
    """LLM-as-judge (usar get_llm('razonamiento'): Gemini/Claude)."""
    veredicto = llm.invoke(f"{RUBRICA_TONO}\n\nRespuesta a evaluar:\n{respuesta}")
    texto = veredicto.content if hasattr(veredicto, "content") else str(veredicto)
    m = re.search(r"[01](?:\.\d+)?", texto)
    return float(m.group()) if m else 0.0


# ── Adaptadores LangSmith (usados por run_eval.py) ──────────────────────────
def ls_adherencia(run, example) -> dict:
    ok = adherencia_precios(run.outputs.get("respuesta", ""),
                            example.outputs["montos_permitidos"])
    return {"key": "adherencia_precios", "score": 1.0 if ok else 0.0}


def ls_extraccion(run, example) -> dict:
    ok = extraccion_correcta(run.outputs.get("calificacion", {}),
                             example.outputs.get("calificacion"))
    return {"key": "extraccion_calificacion", "score": 1.0 if ok else 0.0}


def ls_escalamiento(run, example) -> dict:
    ok = bool(run.outputs.get("escalo")) == example.outputs["debe_escalar"]
    return {"key": "escalamiento_descuento", "score": 1.0 if ok else 0.0}
