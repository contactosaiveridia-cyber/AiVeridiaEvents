"""BPMN timers (event-based gateways) as one-shot schedules.

Each wait node schedules its timeout when the graph parks (PT48H, P7D, PT24H,
PT48H); the competing message event cancels it on arrival (CANCELACIONES).

Backends:
  null         dev/tests/simulator — records calls, fires nothing.
  apscheduler  local dev — in-process one-shot jobs hitting the same callback
               the Scheduler Lambda uses (mock fiel de EventBridge Scheduler).
  eventbridge  prod — EventBridge Scheduler one-shot schedules targeting the
               resume Lambda (see infra/main.tf, scheduler group timers).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from core.config import settings

log = logging.getLogger("aiveridia.timers")

# nodo de espera -> (evento de timeout, horas configuradas)
TIMEOUT_POR_ESPERA: dict[str, tuple[str, Callable[[], float]]] = {
    "enviar_cotizacion": ("timeout_cotizacion", lambda: settings.aiv_timeout_cotizacion_h),
    "enviar_seguimiento": ("timeout_final", lambda: settings.aiv_timeout_final_h),
    "enviar_link_pago": ("timeout_pago", lambda: settings.aiv_timeout_pago_h),
    "recordar_pago": ("timeout_pago_final", lambda: settings.aiv_timeout_pago_final_h),
}

# evento entrante -> timers que deja obsoletos (regla 5: la escalera solo se
# corta porque el cliente respondió/pagó, nunca saltándose peldaños)
CANCELACIONES: dict[str, list[str]] = {
    "mensaje_cliente": ["timeout_cotizacion", "timeout_final"],
    "cliente_acepta": ["timeout_cotizacion", "timeout_final"],
    "pago_ok": ["timeout_pago", "timeout_pago_final"],
}


def _timer_id(tenant_id: str, lead_id: str, evento: str) -> str:
    """EventBridge Scheduler exige Name <= 64 chars con patrón [0-9a-zA-Z-_.]
    (sin ':'); con tenant y lead UUID el nombre plano mediría 77+ chars y el
    truncado colisionaría entre eventos del mismo lead. Se usa un prefijo
    legible + hash determinista (mismo id al programar y al cancelar)."""
    import hashlib
    import re

    base = re.sub(r"[^0-9a-zA-Z_.-]", "_", evento)[:28]
    digest = hashlib.sha256(f"{tenant_id}:{lead_id}:{evento}".encode()).hexdigest()[:24]
    return f"aiv-{base}-{digest}"  # 4 + 28 + 1 + 24 = máx. 57 chars


class NullTimers:
    """Records intent (asserted in tests); nothing fires."""

    def __init__(self) -> None:
        self.programados: list[tuple[str, str, str, datetime]] = []
        self.cancelados: list[tuple[str, str, str]] = []

    def programar(self, tenant_id: str, lead_id: str, evento: str, en: datetime) -> None:
        self.programados.append((tenant_id, lead_id, evento, en))

    def cancelar(self, tenant_id: str, lead_id: str, evento: str) -> None:
        self.cancelados.append((tenant_id, lead_id, evento))


class APSchedulerTimers:
    """Dev mock of EventBridge Scheduler: one-shot in-process jobs."""

    def __init__(self, disparar: Callable[[str, str, str], None]) -> None:
        from apscheduler.schedulers.background import BackgroundScheduler

        self._disparar = disparar
        self._sched = BackgroundScheduler(timezone="UTC")
        self._sched.start()

    def programar(self, tenant_id: str, lead_id: str, evento: str, en: datetime) -> None:
        self._sched.add_job(self._disparar, "date", run_date=en,
                            args=[tenant_id, lead_id, evento],
                            id=_timer_id(tenant_id, lead_id, evento),
                            replace_existing=True)

    def cancelar(self, tenant_id: str, lead_id: str, evento: str) -> None:
        try:
            self._sched.remove_job(_timer_id(tenant_id, lead_id, evento))
        except Exception:
            pass  # ya disparó o nunca existió: cancelar es idempotente

    def shutdown(self) -> None:
        self._sched.shutdown(wait=False)


class EventBridgeTimers:
    """One-shot schedules in the aiveridia-events-timers group (main.tf §6)."""

    def __init__(self) -> None:
        import os

        import boto3

        self._client = boto3.client("scheduler")
        self._group = os.environ["AIV_SCHEDULER_GROUP"]
        self._target_arn = os.environ["AIV_RESUME_LAMBDA_ARN"]
        self._role_arn = os.environ["AIV_SCHEDULER_ROLE_ARN"]

    def programar(self, tenant_id: str, lead_id: str, evento: str, en: datetime) -> None:
        import json

        self._client.create_schedule(
            Name=_timer_id(tenant_id, lead_id, evento),
            GroupName=self._group,
            ScheduleExpression=f"at({en:%Y-%m-%dT%H:%M:%S})",
            FlexibleTimeWindow={"Mode": "OFF"},
            ActionAfterCompletion="DELETE",
            Target={"Arn": self._target_arn, "RoleArn": self._role_arn,
                    "Input": json.dumps({"tenant_id": tenant_id,
                                         "lead_id": lead_id, "evento": evento})},
        )

    def cancelar(self, tenant_id: str, lead_id: str, evento: str) -> None:
        try:
            self._client.delete_schedule(
                Name=_timer_id(tenant_id, lead_id, evento), GroupName=self._group)
        except self._client.exceptions.ResourceNotFoundException:
            pass


_backend = None


def configurar(backend) -> None:
    """Set the process-wide backend (app lifespan / simulator / tests)."""
    global _backend
    _backend = backend


def get_timers():
    global _backend
    if _backend is None:
        _backend = NullTimers()  # default seguro: nada dispara solo
    return _backend


def programar_timeout_de_espera(nodo_espera: str, tenant_id: str, lead_id: str) -> None:
    """Called by each wait node right before the graph parks (END)."""
    evento, horas = TIMEOUT_POR_ESPERA[nodo_espera]
    en = datetime.now(timezone.utc) + timedelta(hours=horas())
    log.info("timer %s para %s:%s a las %s", evento, tenant_id, lead_id, en.isoformat())
    get_timers().programar(tenant_id, lead_id, evento, en)


def cancelar_timers_obsoletos(evento_entrante: str, tenant_id: str, lead_id: str) -> None:
    """Called by the runtime when an external event arrives."""
    for evento in CANCELACIONES.get(evento_entrante, []):
        get_timers().cancelar(tenant_id, lead_id, evento)


def programar_en(tenant_id: str, clave: str, evento: str, en: datetime) -> None:
    """P2: absolute-date timers (D-7, día del evento, +1 día, +10 meses).
    `evento` admite sufijo discriminador, p. ej. 'timeout_proveedor:{id}' para
    los boundary timers por instancia del multi-instance de proveedores."""
    log.info("timer %s para %s:%s a las %s", evento, tenant_id, clave, en.isoformat())
    get_timers().programar(tenant_id, clave, evento, en)


def cancelar(tenant_id: str, clave: str, evento: str) -> None:
    get_timers().cancelar(tenant_id, clave, evento)
