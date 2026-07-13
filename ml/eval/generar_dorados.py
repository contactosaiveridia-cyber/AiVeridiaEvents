"""Genera el dataset de regresión: 50 conversaciones doradas con etiquetas.

Cada item fija el contrato que el modelo (base o fine-tuned) debe cumplir:
  - calificacion  : qué debe extraer A1 (None si no aplica)
  - faltantes     : qué debe repreguntar
  - montos_permitidos : los ÚNICOS montos S/ que pueden aparecer en la
                        respuesta (adherencia a precios = 100%, regla 1)
  - debe_escalar  : si el descuento pedido supera el umbral (10%) -> interrupt

Los precios se calculan con las MISMAS reglas del seed de Los Jazmines
(db/seed.sql): son el gold del evaluador de adherencia.

  python generar_dorados.py            # escribe dataset_regresion.jsonl
"""

import json
from datetime import date

# ── Espejo del seed de Los Jazmines (fuente de verdad: db/seed.sql) ─────────
PAQUETES = {"basica": 1800.0, "clasica": 2800.0, "tematico": 3500.0, "premium": 4500.0}
HOY = "2026-07-12"  # fecha de generación: congela el factor de anticipación


def factor(fecha_iso: str, aforo: int) -> float:
    d = date.fromisoformat(fecha_iso)
    f = 1.0
    if d.month in (12, 1):
        f *= 1.20
    elif d.month == 7:
        f *= 1.10
    dow = (d.weekday() + 1) % 7          # convención Postgres: 0=domingo
    if dow == 6:
        f *= 1.15
    elif dow == 0:
        f *= 1.10
    elif dow in (1, 2, 3, 4):
        f *= 0.90
    if (d - date.fromisoformat(HOY)).days >= 90:
        f *= 0.95
    if aforo >= 100:
        f *= 1.10
    return f


def precio(paquete: str, fecha_iso: str, aforo: int, descuento: float = 0.0) -> float:
    lista = round(PAQUETES[paquete] * factor(fecha_iso, aforo), 2)
    return round(lista * (1 - descuento / 100), 2)


def item(id_, escenario, conversacion, calificacion=None, faltantes=None,
         montos=None, debe_escalar=False):
    return {"id": f"dorada-{id_:02d}", "escenario": escenario, "hoy": HOY,
            "conversacion": conversacion,
            "esperado": {"calificacion": calificacion,
                         "faltantes": faltantes or [],
                         "montos_permitidos": montos or [],
                         "debe_escalar": debe_escalar}}


def c(cliente, agente=None):
    turnos = [{"role": "cliente", "texto": cliente}]
    if agente:
        turnos.append({"role": "agente", "texto": agente})
    return turnos


DORADAS = []
n = 0

# ── 20 cotizaciones directas (fecha+aforo+tipo completos) ───────────────────
COTIZACIONES = [
    ("2026-12-12", 40, "cumpleaños", "clasica"),      # sáb dic: x1.15 x1.20
    ("2026-12-12", 40, "cumpleaños", "tematico"),
    ("2026-09-12", 40, "cumpleaños", "clasica"),      # sáb normal: x1.15
    ("2026-09-12", 30, "baby shower", "basica"),
    ("2026-07-26", 50, "cumpleaños", "clasica"),      # dom jul: x1.10 x1.10
    ("2026-07-30", 35, "bautizo", "basica"),          # jue jul: x1.10 x0.90
    ("2026-08-06", 45, "cumpleaños", "tematico"),     # jue: x0.90
    ("2026-08-08", 120, "promoción escolar", "premium"),  # sáb aforo>=100
    ("2026-09-05", 60, "cumpleaños", "tematico"),
    ("2026-08-30", 80, "quinceañero", "premium"),     # dom: x1.10
    ("2026-12-19", 100, "cumpleaños", "premium"),     # sáb dic aforo
    ("2026-08-04", 25, "baby shower", "basica"),      # mar: x0.90
    ("2026-09-13", 45, "cumpleaños", "clasica"),      # dom: x1.10
    ("2026-08-15", 55, "cumpleaños", "tematico"),     # sáb
    ("2026-07-20", 30, "bautizo", "basica"),          # lun jul
    ("2026-11-07", 90, "aniversario", "premium"),     # sáb (>=90d: x0.95)
    ("2026-12-27", 45, "cumpleaños", "clasica"),      # dom dic (>=90d)
    ("2026-08-22", 40, "cumpleaños", "clasica"),      # sáb
    ("2026-09-19", 70, "quinceañero", "tematico"),    # sáb
    ("2026-08-28", 35, "baby shower", "basica"),      # vie: sin factor dow
]
for fecha, aforo, tipo, paq in COTIZACIONES:
    n += 1
    p = precio(paq, fecha, aforo)
    DORADAS.append(item(
        n, "cotizacion_directa",
        c(f"Hola! quiero cotizar un {tipo} para el {fecha}, "
          f"serían {aforo} personas"),
        calificacion={"fecha_evento": fecha, "aforo": aforo, "tipo_evento": tipo},
        montos=[p]))

# ── 10 con datos incompletos (deben repreguntar, sin mencionar montos) ──────
INCOMPLETAS = [
    ("Hola, ¿hacen fiestas infantiles?", ["fecha_evento", "aforo", "tipo_evento"]),
    ("Quiero una fiesta para mi hijita", ["fecha_evento", "aforo"]),
    ("Cotízame para el 2026-09-12 porfa", ["aforo", "tipo_evento"]),
    ("Somos 40 personas, ¿cuánto sale?", ["fecha_evento", "tipo_evento"]),
    ("Un baby shower, ¿qué precios tienen?", ["fecha_evento", "aforo"]),
    ("Para diciembre quiero algo lindo", ["fecha_evento", "aforo", "tipo_evento"]),
    ("El cumple de mi hijo, unos 30 niños", ["fecha_evento"]),
    ("¿Tienen fechas libres en agosto?", ["fecha_evento", "aforo", "tipo_evento"]),
    ("Quiero el salón grande para un quinceañero", ["fecha_evento", "aforo"]),
    ("Mi engreída cumple añitos pronto 🎉", ["fecha_evento", "aforo"]),
]
for texto, faltantes in INCOMPLETAS:
    n += 1
    DORADAS.append(item(n, "datos_incompletos", c(texto), faltantes=faltantes))

# ── 8 objeciones/FAQ (responder con conocimiento del tenant, sin inventar) ──
FAQS = [
    ("¿El paquete incluye la torta?", []),
    ("¿Puedo llevar mi propia decoración?", []),
    ("¿Puedo llevar mi propio catering?", [150.0]),          # corkage S/150
    ("¿Hasta qué hora puede ser la fiesta?", [250.0]),       # hora extra S/250
    ("¿Aceptan Yape o Plin?", []),
    ("¿Tienen estacionamiento?", []),
    ("¿Qué pasa si llueve el día del evento?", []),
    ("¿Puedo reprogramar si sale un imprevisto?", []),
]
for texto, montos in FAQS:
    n += 1
    DORADAS.append(item(n, "objecion_faq", c(texto), montos=montos))

# ── 6 negociaciones de descuento (umbral 10%) ───────────────────────────────
DESCUENTOS = [(15.0, True), (20.0, True), (12.0, True),
              (5.0, False), (8.0, False), (10.0, False)]
for desc, escala in DESCUENTOS:
    n += 1
    p_lista = precio("clasica", "2026-09-12", 40)
    montos = [] if escala else [p_lista, precio("clasica", "2026-09-12", 40, desc)]
    DORADAS.append(item(
        n, "negociacion_descuento",
        c(f"Cotiza un cumpleaños el 2026-09-12 para 40 personas",
          f"¡Claro! El paquete Fiesta Clásica sale S/ {p_lista:.2f} para esa fecha.")
        + [{"role": "cliente", "texto": f"¿Me puedes hacer {desc:.0f}% de descuentito?"}],
        calificacion={"fecha_evento": "2026-09-12", "aforo": 40,
                      "tipo_evento": "cumpleaños"},
        montos=montos, debe_escalar=escala))

# ── 6 fechas ocupadas (ofrecer alternativas, sin montos) ────────────────────
OCUPADAS = ["2026-12-05", "2026-12-19", "2026-10-31", "2026-09-26",
            "2026-08-01", "2026-12-24"]
for fecha in OCUPADAS:
    n += 1
    DORADAS.append(item(
        n, "fecha_ocupada",
        c(f"Quiero el {fecha} para un cumpleaños de 40 personas",
          "Ay, esa fecha ya está separada 😔 pero tengo fechas cercanas libres.")
        + [{"role": "cliente", "texto": "¿Qué otras fechas tienes?"}],
        calificacion={"fecha_evento": fecha, "aforo": 40,
                      "tipo_evento": "cumpleaños"}))

assert len(DORADAS) == 50, f"deben ser 50, hay {len(DORADAS)}"

if __name__ == "__main__":
    from pathlib import Path
    destino = Path(__file__).parent / "dataset_regresion.jsonl"
    with open(destino, "w", encoding="utf-8") as fh:
        for d in DORADAS:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"50 conversaciones doradas -> {destino}")
