"""Tests del pipeline ML (ml/): anonimización, curaduría y dataset dorado.

El dataset de regresión es un artefacto versionado: estos tests garantizan que
sus golds son consistentes con las reglas de precio del seed y que los
evaluadores detectan violaciones (especialmente adherencia a precios, regla 1).
"""

import json
import sys
from pathlib import Path

ML = Path(__file__).parents[3] / "ml"
sys.path.insert(0, str(ML / "dataset"))
sys.path.insert(0, str(ML / "eval"))

from anonimizar import anonimizar_texto  # noqa: E402
from curar import a_sharegpt, curar, parse_whatsapp_export  # noqa: E402
from evaluadores import adherencia_precios, extraccion_correcta, montos_en  # noqa: E402
from generar_dorados import DORADAS, precio  # noqa: E402


# ── Anonimización ───────────────────────────────────────────────────────────
def test_anonimiza_telefonos_dni_emails_direcciones():
    crudo = ("Soy Roxana, mi cel es 987 654 321, DNI 45678912, "
             "escríbeme a roxi@gmail.com, vivo en Av. España 1234, Trujillo")
    limpio = anonimizar_texto(crudo, nombres_adultos=["Roxana"])
    assert "987 654 321" not in limpio and "45678912" not in limpio
    assert "roxi@gmail.com" not in limpio and "Av. España" not in limpio
    assert "Roxana" not in limpio
    assert "XXXXXXXX" in limpio and "[DIRECCIÓN]" in limpio


def test_pseudonimo_es_determinista_y_coherente():
    a = anonimizar_texto("Roxana dijo que Roxana confirma", nombres_adultos=["Roxana"])
    b = anonimizar_texto("Roxana pagó", nombres_adultos=["Roxana"])
    pseudo = a.split()[0]
    assert a == f"{pseudo} dijo que {pseudo} confirma"
    assert b.startswith(pseudo)          # mismo nombre -> mismo pseudónimo


# ── Curaduría ───────────────────────────────────────────────────────────────
EXPORT = """12/05/26, 14:03 - Roxana Cliente: hola, precios porfa
12/05/26, 14:05 - +51944123123: ¡Hola! Claro que sí 😊
para cuándo sería?
12/05/26, 14:06 - Roxana Cliente: el 15 de agosto
12/05/26, 14:07 - +51944123123: ¡Anotado!
"""


def test_parse_whatsapp_export_roles_y_rafagas():
    turnos = parse_whatsapp_export(EXPORT, "+51944123123")
    assert [r for r, _ in turnos] == ["human", "gpt", "human", "gpt"]
    assert "para cuándo sería?" in turnos[1][1]     # continuación pegada


def test_sharegpt_multi_turno_con_system():
    ejemplo = a_sharegpt(parse_whatsapp_export(EXPORT, "+51944123123"), "convertido")
    assert ejemplo["conversations"][0]["from"] == "system"
    assert ejemplo["label"] == "convertido"


def test_oversample_de_conversaciones_ganadoras(tmp_path):
    ruta = tmp_path / "chat.txt"
    ruta.write_text(EXPORT, encoding="utf-8")
    ganadora = curar([(ruta, "convertido")], "+51944123123")
    perdida = curar([(ruta, "perdido")], "+51944123123")
    assert len(ganadora) == 2 and len(perdida) == 1    # x2 las que cierran


# ── Dataset dorado ──────────────────────────────────────────────────────────
def test_dataset_dorado_50_items_y_archivo_sincronizado():
    assert len(DORADAS) == 50
    ruta = ML / "eval" / "dataset_regresion.jsonl"
    en_disco = [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines()]
    assert en_disco == DORADAS                          # el jsonl no está desfasado


def test_precios_gold_siguen_las_reglas_del_seed():
    # Clásica, sábado de diciembre a 153 días (anticipación >= 90):
    # 2800 * 1.15 (sáb) * 1.20 (dic) * 0.95 (anticipación) = 3670.80
    assert precio("clasica", "2026-12-12", 40) == 3670.8
    # Básica, jueves de julio: 1800 * 1.10 * 0.90 = 1782
    assert precio("basica", "2026-07-30", 35) == 1782.0
    # Premium, sábado con aforo 120: 4500 * 1.15 * 1.10 = 5692.50
    assert precio("premium", "2026-08-08", 120) == 5692.5


def test_escalamiento_gold_consistente_con_umbral():
    negociaciones = [d for d in DORADAS if d["escenario"] == "negociacion_descuento"]
    assert len(negociaciones) == 6
    for d in negociaciones:
        pedido = float(d["conversacion"][-1]["texto"].split("%")[0].split()[-1])
        assert d["esperado"]["debe_escalar"] == (pedido > 10.0)


def test_incompletas_no_permiten_montos():
    for d in DORADAS:
        if d["escenario"] == "datos_incompletos":
            assert d["esperado"]["montos_permitidos"] == []
            assert d["esperado"]["faltantes"]


# ── Evaluadores ─────────────────────────────────────────────────────────────
def test_adherencia_detecta_precio_inventado():
    assert adherencia_precios("El paquete sale S/ 3864.00, ¿separamos?", [3864.0])
    assert not adherencia_precios("Te lo dejo en S/ 3500.00 😉", [3864.0])
    assert adherencia_precios("¡Con gusto te ayudo! ¿Para qué fecha?", [3864.0])
    assert not adherencia_precios("Son S/ 3,864.00 más S/ 99.90 de garantía", [3864.0])


def test_montos_en_normaliza_formatos():
    assert montos_en("S/ 3,864.00 y S/. 150") == [3864.0, 150.0]


def test_extraccion_correcta_compara_campos():
    gold = {"fecha_evento": "2026-09-12", "aforo": 40, "tipo_evento": "cumpleaños"}
    assert extraccion_correcta(
        {"fecha_evento": "2026-09-12", "aforo": 40,
         "tipo_evento": "Cumpleaños infantil"}, gold)
    assert not extraccion_correcta(
        {"fecha_evento": "2026-09-13", "aforo": 40, "tipo_evento": "cumpleaños"}, gold)
    assert extraccion_correcta({}, None)               # escenarios sin gold
