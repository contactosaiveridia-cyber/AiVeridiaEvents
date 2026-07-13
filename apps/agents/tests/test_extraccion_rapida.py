"""Parser determinista de respaldo de la calificación (A1): rellena lo que la
extracción del LLM dejó en blanco."""

from datetime import date, timedelta

import pytest

from graphs.graph_comercial import _extraccion_rapida

FUTURO = date.today() + timedelta(days=400)


@pytest.mark.parametrize("texto,esperado", [
    (f"la fecha es {FUTURO.isoformat()} y vendrían 40 niños",
     {"fecha_evento": FUTURO, "aforo": 40}),
    (f"para el {FUTURO.day}/{FUTURO.month:02d}/{FUTURO.year}, unas 80 personas",
     {"fecha_evento": FUTURO, "aforo": 80}),
    ("es un cumpleaños con 50 invitados",
     {"tipo_evento": "cumpleaños", "aforo": 50}),
    ("un baby shower", {"tipo_evento": "baby shower"}),
    ("hola, quiero información", {}),
])
def test_extraccion_rapida(texto, esperado):
    assert _extraccion_rapida(texto) == esperado


def test_fecha_en_espanol():
    r = _extraccion_rapida("sería el 15 de agosto de 2030")
    assert r == {"fecha_evento": date(2030, 8, 15)}


def test_fechas_pasadas_se_descartan():
    assert _extraccion_rapida("fue el 2020-01-15") == {}
    assert _extraccion_rapida("el 31 de febrero de 2030") == {}  # inválida
