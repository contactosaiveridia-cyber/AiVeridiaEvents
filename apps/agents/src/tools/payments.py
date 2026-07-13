"""Payment links (BPMN Task_LinkPago) and payment registration.

F2: deterministic core + dev stub link. The real Culqi/MercadoPago gateway
integration (signature-verified webhook) lands in F3; this module keeps the
same contract so the graph does not change.
"""

from datetime import datetime, timedelta, timezone

from core.config import settings
from core.db import tenant_connection

SEPARACION_PCT = 0.30       # adelanto de separación: 30% del precio final
VENCIMIENTO_LINK_HORAS = 72  # 24 h + recordatorio + 48 h (escalera P1)


def crear_link_separacion(tenant_id: str, lead_id: str, precio_final: float) -> dict:
    """Creates the pending 'separacion' payment attached to the lead's hold and
    returns {"url", "pago_id", "monto"}."""
    monto = round(precio_final * SEPARACION_PCT, 2)
    vence = datetime.now(timezone.utc) + timedelta(hours=VENCIMIENTO_LINK_HORAS)

    with tenant_connection(tenant_id) as conn:
        reserva = conn.execute(
            "select id from reservas where lead_id = %s and estado = 'hold'",
            (lead_id,),
        ).fetchone()
        if reserva is None:
            raise RuntimeError(f"lead {lead_id}: no hay hold para generar link")
        pago = conn.execute(
            """insert into pagos (tenant_id, reserva_id, concepto, monto, estado, vence_en)
               values (%s, %s, 'separacion', %s, 'pendiente', %s) returning id""",
            (tenant_id, reserva["id"], monto, vence),
        ).fetchone()

    pago_id = str(pago["id"])
    if settings.aiv_env == "prod":
        raise NotImplementedError("F3: integración Culqi/MercadoPago")
    url = f"https://pagos.aiveridia.dev/separacion/{pago_id}"  # stub dev
    return {"url": url, "pago_id": pago_id, "monto": monto}


def marcar_pago_recibido(tenant_id: str, lead_id: str, pasarela_ref: str,
                         medio: str = "tarjeta") -> None:
    """Called on pago_ok (webhook in F3, simulator in dev)."""
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            """update pagos p set estado = 'pagado', pagado_en = now(),
                                  medio = %s, pasarela_ref = %s
                from reservas r
               where p.reserva_id = r.id and r.lead_id = %s
                 and p.concepto = 'separacion' and p.estado = 'pendiente'""",
            (medio, pasarela_ref, lead_id),
        )


def vencer_pago(tenant_id: str, lead_id: str) -> None:
    """Called by liberar_fecha after the reminder ladder is exhausted."""
    with tenant_connection(tenant_id) as conn:
        conn.execute(
            """update pagos p set estado = 'vencido'
                from reservas r
               where p.reserva_id = r.id and r.lead_id = %s
                 and p.concepto = 'separacion' and p.estado = 'pendiente'""",
            (lead_id,),
        )
