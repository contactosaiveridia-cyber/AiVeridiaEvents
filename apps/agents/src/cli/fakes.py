"""In-memory fakes of the deterministic tools, for running the CLI simulator
on machines without Postgres (AIV_FAKE_DB=1). Same contracts, seed-like data.
The real tools hit the database and are covered by the DB test suite."""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import tools.bookings as bookings
import tools.contracts as contracts
import tools.crm as crm
import tools.payments as payments
import tools.pricing as pricing

ESPACIOS = [
    {"id": "e001", "nombre": "Sala Kids", "aforo_max": 50},
    {"id": "e002", "nombre": "Salón Jardín", "aforo_max": 80},
    {"id": "e003", "nombre": "Salón Principal", "aforo_max": 150},
]
PAQUETES = [
    {"id": "p001", "nombre": "Fiesta Básica", "precio_base": 1800.0,
     "incluye": ["local 4h", "mesas y sillas", "sonido básico"]},
    {"id": "p002", "nombre": "Fiesta Clásica", "precio_base": 2800.0,
     "incluye": ["local 5h", "decoración estándar", "torta 30p", "mozo"]},
    {"id": "p003", "nombre": "Temático Kids Total", "precio_base": 3500.0,
     "incluye": ["local 5h", "decoración temática", "animación 3h", "inflables"]},
    {"id": "p004", "nombre": "Fiesta Premium", "precio_base": 4500.0,
     "incluye": ["local 6h", "todo incluido", "hora loca", "fotografía"]},
]
REGLAS = [
    {"tipo": "temporada", "condicion": {"meses": [12, 1], "factor": 1.20}, "prioridad": 10},
    {"tipo": "dia_semana", "condicion": {"dow": [6], "factor": 1.15}, "prioridad": 30},
    {"tipo": "dia_semana", "condicion": {"dow": [1, 2, 3, 4], "factor": 0.90}, "prioridad": 32},
]

# fechas ya ocupadas para poder demostrar el camino de alternativas
OCUPADAS: set[date] = {date.today() + timedelta(days=14)}
_holds: dict[str, date] = {}          # lead_id -> fecha
_leads: dict[str, dict] = {}


def _espacio_para(aforo: int):
    return next((e for e in ESPACIOS if e["aforo_max"] >= aforo), None)


def check_availability(tenant_id, fecha, aforo):
    espacio = _espacio_para(aforo)
    libre = espacio is not None and fecha not in OCUPADAS and fecha not in _holds.values()
    return {"libre": libre,
            "espacio_id": espacio["id"] if espacio else None,
            "espacio_nombre": espacio["nombre"] if espacio else None}


def nearest_free_dates(tenant_id, fecha, aforo, n=3):
    out, c = [], fecha
    while len(out) < n:
        c += timedelta(days=1)
        if c not in OCUPADAS and c not in _holds.values():
            out.append(c.isoformat())
    return out


def create_hold(tenant_id, lead_id, espacio_id, fecha, cotizacion_id=None):
    if fecha in OCUPADAS or fecha in _holds.values():
        return None
    _holds[lead_id] = fecha
    return f"hold-{uuid.uuid4().hex[:8]}"


def confirm_booking(tenant_id, lead_id):
    if lead_id not in _holds:
        raise RuntimeError("no hay hold")
    OCUPADAS.add(_holds.pop(lead_id))
    return f"rsv-{uuid.uuid4().hex[:8]}"


def release_hold(tenant_id, lead_id):
    _holds.pop(lead_id, None)


def cotizar(tenant_id, calificacion, descuento_solicitado=0.0):
    paquete = pricing._elegir_paquete(PAQUETES, calificacion.presupuesto_max)
    factor = Decimal("1")
    for r in REGLAS:
        if pricing._aplica(r, calificacion.fecha_evento, calificacion.aforo):
            factor *= Decimal(str(r["condicion"]["factor"]))
    lista = float(Decimal(str(paquete["precio_base"])) * factor)
    desc = min(max(descuento_solicitado, 0.0), pricing.DESCUENTO_MAX_ABSOLUTO)
    return {"paquete_id": paquete["id"], "paquete_nombre": paquete["nombre"],
            "incluye": paquete["incluye"], "precio_lista": round(lista, 2),
            "descuento_pct": desc,
            "precio_final": round(lista * (1 - desc / 100), 2),
            "valida_hasta": datetime.now(timezone.utc) + timedelta(hours=48)}


def instalar():
    """Monkeypatch the DB-backed tools with the in-memory fakes."""
    bookings.check_availability = check_availability
    bookings.nearest_free_dates = nearest_free_dates
    bookings.create_hold = create_hold
    bookings.confirm_booking = confirm_booking
    bookings.release_hold = release_hold
    pricing.cotizar = cotizar
    pricing.registrar_cotizacion = lambda *a, **k: f"cot-{uuid.uuid4().hex[:8]}"
    payments.crear_link_separacion = lambda t, lead, precio: {
        "url": f"https://pagos.aiveridia.dev/separacion/demo-{lead[:8]}",
        "pago_id": "demo", "monto": round(precio * 0.30, 2)}
    payments.marcar_pago_recibido = lambda *a, **k: None
    payments.vencer_pago = lambda *a, **k: None
    contracts.generar_contrato_pdf = lambda t, b: f"contratos/contrato_{b}.md"
    crm.ensure_lead = lambda *a, **k: None
    crm.actualizar_calificacion = lambda *a, **k: _leads.setdefault("x", {})
    crm.marcar_cotizado = lambda *a, **k: None
    crm.marcar_seguimiento = lambda *a, **k: None
    crm.to_nurturing = lambda *a, **k: None
