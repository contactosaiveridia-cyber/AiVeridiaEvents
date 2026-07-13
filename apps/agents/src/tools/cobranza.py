"""Installment schedule & reconciliation (BPMN A7). Deterministic: amounts come
from the confirmed quote, never from the LLM.

saldo = precio_final - separación (30% ya pagada). Con más de 60 días por
delante se divide en 2 cuotas (mitad de camino y D-7); si no, una sola a D-7.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from core.db import tenant_connection


def _a_ts(fecha: date) -> datetime:
    return datetime.combine(fecha, time(12, 0), tzinfo=timezone.utc)


def generar_cronograma(tenant_id: str, booking_id: str) -> dict:
    with tenant_connection(tenant_id) as conn:
        datos = conn.execute(
            """select r.inicio::date as fecha_evento, c.precio_final
                 from reservas r left join cotizaciones c on c.id = r.cotizacion_id
                where r.id = %s""",
            (booking_id,),
        ).fetchone()
        if datos is None or datos["precio_final"] is None:
            raise RuntimeError(f"reserva {booking_id} sin cotización asociada")

        total = Decimal(str(datos["precio_final"]))
        saldo = (total * Decimal("0.70")).quantize(Decimal("0.01"))
        fecha_evento: date = datos["fecha_evento"]
        d7 = fecha_evento - timedelta(days=7)
        hoy = date.today()

        if (fecha_evento - hoy).days > 60:
            mitad = hoy + (d7 - hoy) / 2
            cuotas = [(saldo / 2, mitad), (saldo - saldo / 2, d7)]
        else:
            cuotas = [(saldo, d7)]

        plan = []
        for monto, vence in cuotas:
            conn.execute(
                """insert into pagos (tenant_id, reserva_id, concepto, monto,
                                      estado, vence_en)
                   values (%s, %s, 'cuota', %s, 'pendiente', %s)""",
                (tenant_id, booking_id, monto, _a_ts(vence)),
            )
            plan.append({"monto": float(monto), "vence_en": vence.isoformat()})
        return {"cuotas": plan, "saldo": float(saldo),
                "fecha_evento": fecha_evento.isoformat()}


def registrar_pago_cuota(tenant_id: str, booking_id: str, ref: str,
                         medio: str = "yape", monto: float | None = None) -> float:
    """Concilia contra el MONTO realmente recibido y devuelve el saldo restante.

    Con monto: se aplican cuotas completas en orden de vencimiento; un pago
    parcial reduce la cuota pendiente y registra una fila pagada por lo
    efectivamente recibido — el saldo nunca cae más de lo que entró en caja.
    Sin monto (conciliación manual del dueño): la cuota más antigua se marca
    completa, decisión explícita del humano, no default silencioso."""
    with tenant_connection(tenant_id) as conn:
        if monto is None:
            conn.execute(
                """update pagos set estado = 'pagado', pagado_en = now(),
                                    medio = %s, pasarela_ref = %s
                    where id = (select id from pagos
                                 where reserva_id = %s and concepto = 'cuota'
                                   and estado = 'pendiente'
                                 order by vence_en limit 1)""",
                (medio, ref, booking_id),
            )
        else:
            restante = Decimal(str(monto))
            pendientes = conn.execute(
                """select id, monto from pagos
                    where reserva_id = %s and concepto = 'cuota'
                      and estado = 'pendiente' order by vence_en""",
                (booking_id,),
            ).fetchall()
            for cuota in pendientes:
                if restante <= 0:
                    break
                monto_cuota = Decimal(str(cuota["monto"]))
                if restante >= monto_cuota:
                    conn.execute(
                        """update pagos set estado = 'pagado', pagado_en = now(),
                                            medio = %s, pasarela_ref = %s
                            where id = %s""",
                        (medio, ref, cuota["id"]),
                    )
                    restante -= monto_cuota
                else:
                    conn.execute(
                        "update pagos set monto = %s where id = %s",
                        (monto_cuota - restante, cuota["id"]),
                    )
                    conn.execute(
                        """insert into pagos (tenant_id, reserva_id, concepto,
                                              monto, medio, pasarela_ref,
                                              estado, pagado_en)
                           values (%s, %s, 'cuota', %s, %s, %s, 'pagado', now())""",
                        (tenant_id, booking_id, restante, medio, ref),
                    )
                    restante = Decimal("0")
    return saldo_pendiente(tenant_id, booking_id)


def saldo_pendiente(tenant_id: str, booking_id: str) -> float:
    with tenant_connection(tenant_id) as conn:
        row = conn.execute(
            """select coalesce(sum(monto), 0) as saldo from pagos
                where reserva_id = %s and concepto in ('cuota', 'saldo')
                  and estado = 'pendiente'""",
            (booking_id,),
        ).fetchone()
    return float(row["saldo"])
