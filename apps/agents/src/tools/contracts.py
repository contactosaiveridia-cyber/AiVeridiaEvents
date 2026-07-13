"""Digital contract generation (BPMN Task_Contrato).

F2: deterministic markdown contract written locally (dev). F8 moves storage to
S3 (assets bucket) keeping the same return contract (a URL/path string).
"""

from datetime import datetime, timezone
from pathlib import Path

from core.config import settings
from core.db import tenant_connection

CONTRATOS_DIR = Path("contratos")


def generar_contrato_pdf(tenant_id: str, booking_id: str) -> str:
    with tenant_connection(tenant_id) as conn:
        datos = conn.execute(
            """select r.id, r.inicio, r.fin, t.nombre as salon,
                      e.nombre as espacio, l.nombre as cliente, l.telefono,
                      c.precio_final, p.nombre as paquete
                 from reservas r
                 join tenants t on t.id = r.tenant_id
                 join espacios e on e.id = r.espacio_id
                 join leads l on l.id = r.lead_id
                 left join cotizaciones c on c.id = r.cotizacion_id
                 left join paquetes p on p.id = c.paquete_id
                where r.id = %s""",
            (booking_id,),
        ).fetchone()
        if datos is None:
            raise RuntimeError(f"reserva {booking_id} no encontrada")

        contenido = f"""# Contrato de reserva — {datos['salon']}

- **Reserva:** {datos['id']}
- **Cliente:** {datos['cliente'] or 'por completar'} ({datos['telefono'] or 's/tel'})
- **Espacio:** {datos['espacio']}
- **Paquete:** {datos['paquete'] or 'personalizado'}
- **Fecha del evento:** {datos['inicio']:%d/%m/%Y}
- **Monto total:** S/ {datos['precio_final'] or 0:.2f}
- **Emitido:** {datetime.now(timezone.utc):%d/%m/%Y %H:%M} UTC

El presente documento confirma la separación de la fecha indicada conforme a
las políticas de reprogramación y reglas del local entregadas al cliente.
"""

    if settings.aiv_env == "prod":
        raise NotImplementedError("F8: subida a S3 (assets bucket)")
    CONTRATOS_DIR.mkdir(exist_ok=True)
    destino = CONTRATOS_DIR / f"contrato_{booking_id}.md"
    destino.write_text(contenido, encoding="utf-8")

    with tenant_connection(tenant_id) as conn:
        conn.execute(
            "update reservas set contrato_url = %s where id = %s",
            (str(destino), booking_id),
        )
    return str(destino)
