"""Deterministic pricing (BPMN businessRuleTask Task_Cotizar).

The LLM NEVER sets prices. precio_lista = precio_base * product(factors) from
reglas_precio, evaluated here in code. Discounts arrive as a request (client
negotiation), are capped in code, and anything above AIV_UMBRAL_DESCUENTO
triggers the owner interrupt in the graph — never silently applied.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from core.config import settings
from core.db import tenant_connection

VALIDEZ_COTIZACION_HORAS = 48
DESCUENTO_MAX_ABSOLUTO = 30.0  # hard cap: guard against any runaway path


def _aplica(regla: dict, fecha: date, aforo: int) -> bool:
    cond, tipo = regla["condicion"], regla["tipo"]
    if tipo == "temporada":
        return fecha.month in cond.get("meses", [])
    if tipo == "dia_semana":
        dow_pg = (fecha.weekday() + 1) % 7  # convención Postgres: 0=domingo
        return dow_pg in cond.get("dow", [])
    if tipo == "anticipacion":
        return (fecha - date.today()).days >= cond.get("min_dias", 10**6)
    if tipo == "aforo":
        return aforo >= cond.get("min_aforo", 10**6)
    return False


def _elegir_paquete(paquetes: list[dict], presupuesto: float | None) -> dict:
    """MVP heuristic: fit to budget when stated, otherwise mid-tier package.
    Selection only affects what gets offered first; the price itself always
    comes from reglas_precio."""
    orden = sorted(paquetes, key=lambda p: p["precio_base"])
    if presupuesto:
        bajo_presupuesto = [p for p in orden if float(p["precio_base"]) <= presupuesto]
        return bajo_presupuesto[-1] if bajo_presupuesto else orden[0]
    return orden[len(orden) // 2]


def cotizar(tenant_id: str, calificacion, descuento_solicitado: float = 0.0) -> dict:
    fecha: date = calificacion.fecha_evento
    aforo: int = calificacion.aforo

    with tenant_connection(tenant_id) as conn:
        paquetes = conn.execute(
            "select id, nombre, precio_base, incluye from paquetes where activo"
        ).fetchall()
        reglas = conn.execute(
            "select tipo, condicion, prioridad from reglas_precio order by prioridad"
        ).fetchall()

        if not paquetes:
            raise ValueError(f"tenant {tenant_id} sin paquetes activos")

        paquete = _elegir_paquete(paquetes, calificacion.presupuesto_max)

        factor = Decimal("1")
        for regla in reglas:
            if _aplica(regla, fecha, aforo):
                factor *= Decimal(str(regla["condicion"].get("factor", 1)))

        precio_lista = (Decimal(str(paquete["precio_base"])) * factor).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        descuento = min(max(descuento_solicitado, 0.0), DESCUENTO_MAX_ABSOLUTO)
        precio_final = (
            precio_lista * (Decimal("1") - Decimal(str(descuento)) / 100)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        valida_hasta = datetime.now(timezone.utc) + timedelta(
            hours=VALIDEZ_COTIZACION_HORAS
        )

        return {
            "paquete_id": str(paquete["id"]),
            "paquete_nombre": paquete["nombre"],
            "incluye": paquete["incluye"],
            "precio_lista": float(precio_lista),
            "descuento_pct": descuento,
            "precio_final": float(precio_final),
            "valida_hasta": valida_hasta,
        }


def registrar_cotizacion(tenant_id: str, lead_id: str, q: dict) -> str:
    """Persist the quote (auditable input for GW_Descuento and the dashboard)."""
    with tenant_connection(tenant_id) as conn:
        row = conn.execute(
            """insert into cotizaciones
                   (tenant_id, lead_id, paquete_id, precio_lista, descuento_pct,
                    precio_final, valida_hasta)
               values (%s, %s, %s, %s, %s, %s, %s) returning id""",
            (tenant_id, lead_id, q["paquete_id"], q["precio_lista"],
             q["descuento_pct"], q["precio_final"], q["valida_hasta"]),
        ).fetchone()
        return str(row["id"])


def umbral_descuento() -> float:
    return settings.aiv_umbral_descuento
