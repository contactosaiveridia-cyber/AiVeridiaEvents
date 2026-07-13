"""Anonymization schema for real WhatsApp sales transcripts (dataset source 1).

Todo dato personal se reemplaza ANTES de que la transcripción toque el pipeline
de entrenamiento:
  - teléfonos peruanos           -> +51 9XX XXX XXX sintético estable
  - DNI (8 dígitos)              -> XXXXXXXX
  - emails                       -> cliente@ejemplo.pe
  - direcciones (Av./Jr./Calle)  -> [DIRECCIÓN]
  - nombres conocidos (del CRM)  -> pseudónimo determinista (mismo nombre ->
                                    mismo pseudónimo en toda la conversación,
                                    para no romper la coherencia multi-turno)

Los montos NO se anonimizan: la adherencia a precios es parte del aprendizaje.
"""

import hashlib
import re

PSEUDONIMOS = [
    "María", "Rosa", "Carmen", "Julia", "Ana", "Luz", "Sofía", "Elena",
    "Carlos", "José", "Luis", "Jorge", "Miguel", "Pedro", "Juan", "Víctor",
]
PSEUDONIMOS_NINOS = [
    "Valentina", "Luciana", "Camila", "Mía", "Thiago", "Mateo", "Gael", "Liam",
]

RE_TELEFONO = re.compile(r"(\+?51[\s.-]?)?9\d{2}[\s.-]?\d{3}[\s.-]?\d{3}\b")
RE_DNI = re.compile(r"\b\d{8}\b")
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
RE_DIRECCION = re.compile(
    r"\b(?:Av\.?|Avenida|Jr\.?|Jirón|Calle|Mz\.?|Urb\.?|Pasaje)\s+[^,.\n]{3,40}",
    re.IGNORECASE)


def _pseudonimo(nombre: str, ninos: bool = False) -> str:
    lista = PSEUDONIMOS_NINOS if ninos else PSEUDONIMOS
    indice = int(hashlib.sha256(nombre.lower().encode()).hexdigest(), 16) % len(lista)
    return lista[indice]


def _telefono_sintetico(match: re.Match) -> str:
    estable = int(hashlib.sha256(match.group().encode()).hexdigest(), 16)
    return f"+51 9{estable % 100:02d} {estable % 1000:03d} {estable % 999:03d}"


def anonimizar_texto(texto: str, nombres_adultos: list[str] | None = None,
                     nombres_ninos: list[str] | None = None) -> str:
    texto = RE_TELEFONO.sub(_telefono_sintetico, texto)
    texto = RE_DNI.sub("XXXXXXXX", texto)
    texto = RE_EMAIL.sub("cliente@ejemplo.pe", texto)
    texto = RE_DIRECCION.sub("[DIRECCIÓN]", texto)
    for nombre in nombres_adultos or []:
        texto = re.sub(rf"\b{re.escape(nombre)}\b", _pseudonimo(nombre), texto,
                       flags=re.IGNORECASE)
    for nombre in nombres_ninos or []:
        texto = re.sub(rf"\b{re.escape(nombre)}\b", _pseudonimo(nombre, ninos=True),
                       texto, flags=re.IGNORECASE)
    return texto
