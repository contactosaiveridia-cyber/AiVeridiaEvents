# Lectura de los BPMN P1/P2 y ambigüedades detectadas

## Lectura (contrato semántico)

**P1 — Convertir lead en reserva confirmada** (4 lanes: A1 ventas, A2 cotización, A3 seguimiento, A4 reservas/pagos).
Lead por WhatsApp → `Task_Calificar` (fecha, aforo, tipo, presupuesto) → `Task_Disponibilidad` (query determinista) →
`GW_Disponible`: No → alternativas → espera `Evt_Eleccion` → re-consulta; Sí → `Task_Cotizar` (businessRule, reglas_precio) →
`GW_Descuento` > umbral → `Task_AprobarDescuento` (dueño, interrupt) → `Task_EnviarCotizacion` → espera event-based
(`Evt_Acepta1` | timer 48 h) → seguimiento → espera (`Evt_Acepta2` | timer 7 d → nurturing → `End_NoConvertido`).
Aceptación → `Task_LinkPago` → espera (`Evt_Pago` | 24 h → recordatorio → espera (`Evt_Pago2` | 48 h → `Task_Liberar` →
`End_Cancelada`)). Pago → `Task_Registrar` (bloqueo transaccional) → `Task_Contrato` → `Task_EnviarConf` →
`End_Confirmada` (message end: dispara P2).

**P2 — Ejecutar y fidelizar** (lanes A5 proveedores, A7 cobranza, A6 logística, A8/A9 post-evento).
`Start_Reserva` (evento `reserva.confirmada`) → parallel split: (a) notificar proveedores **multi-instancia** → recibir
confirmaciones con **boundary timer 48 h → `Task_Escalar` al dueño** (sustituto); (b) cronograma de cuotas →
recordatorios secuenciales → conciliar → saldo pendiente a D-7 → notificar. Join paralelo → timer D-7 → checklist →
timer día del evento → montaje (userTask) → timer +1 d → NPS → métricas A9 → timer +10 meses → campaña renovación →
`End_Ciclo`.

## Ambigüedades y resolución adoptada

1. **P1 no modela el bucle de re-pregunta** cuando la calificación está incompleta; `graph_comercial.py` sí (END-espera +
   re-entrada `mensaje_cliente`). Se adopta el bucle del código (coincide con el criterio de aceptación).
2. **El "hold" de fecha no aparece como tarea en P1**, pero `Task_Liberar` implica que existe. Resolución: `Task_LinkPago`
   crea la reserva con `estado='hold'`; `Task_Registrar` la promueve a `confirmada`; `Task_Liberar` la cancela con auditoría.
3. **Origen del descuento**: `Task_Cotizar` es determinista; el descuento entra como solicitud del cliente/negociación,
   validado por código (cap = `AIV_UMBRAL_DESCUENTO`); nunca lo fija el LLM.
4. **Rechazo explícito del cliente** no tiene evento propio: se trata como `mensaje_cliente` y A1 re-clasifica.
5. **P2 `GW_Saldo`**: el chequeo "a 7 días" no tiene timer propio en la lane A7; se evalúa al conciliar cada cuota y en un
   chequeo programado a D-7.
6. **Boundary timer de proveedores es por instancia**: se implementa con Send API (una rama por proveedor) y escalación
   individual (interrupt al dueño) a las 48 h del proveedor que no confirmó.
7. **`Evt_TimerPost` (P1D)** se programa respecto a la fecha del evento (+1 día), no a la finalización del userTask montaje.
8. **`conversaciones.thread_id` es único global** en el esquema (correcto: incluye el prefijo `{tenant_id}:`).
