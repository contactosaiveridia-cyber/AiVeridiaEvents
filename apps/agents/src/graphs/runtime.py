"""Event-driven runtime around the P1 (comercial) and P2 (operativo) graphs.

The webhooks (F3), the Scheduler resume Lambda and the CLI simulator all speak
this same API: fire an event at a thread, or answer a pending interrupt.

thread_id P1 = "{tenant_id}:{lead_id}"        (aislamiento por tenant)
thread_id P2 = "{tenant_id}:{booking_id}:p2"  (un ciclo operativo por reserva)
"""

from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from graphs.graph_comercial import ESTADO_INICIAL, ENTRYPOINTS_RESUME


def thread_config(tenant_id: str, lead_id: str) -> dict:
    return {"configurable": {"thread_id": f"{tenant_id}:{lead_id}",
                             "tenant_id": tenant_id}}


def thread_config_p2(tenant_id: str, booking_id: str) -> dict:
    return {"configurable": {"thread_id": f"{tenant_id}:{booking_id}:p2",
                             "tenant_id": tenant_id}}


def _invocar(graph, config, entrada: dict, estado_inicial: dict) -> dict:
    if not graph.get_state(config).values:  # thread nuevo
        entrada = {**estado_inicial, **entrada}
    return graph.invoke(entrada, config)


def procesar_evento(graph, tenant_id: str, lead_id: str, evento: str,
                    texto: str | None = None,
                    payload: dict[str, Any] | None = None) -> dict:
    """P1: fire a BPMN event (see ENTRYPOINTS_RESUME) at the lead's thread."""
    if evento not in ENTRYPOINTS_RESUME:
        raise ValueError(f"evento desconocido: {evento!r}")

    from tools.timers import cancelar_timers_obsoletos
    cancelar_timers_obsoletos(evento, tenant_id, lead_id)

    entrada: dict[str, Any] = {"tenant_id": tenant_id, "lead_id": lead_id,
                               "evento_entrante": evento}
    if texto is not None:
        entrada["messages"] = [HumanMessage(content=texto)]
    if payload:
        entrada.update(payload)
    return _invocar(graph, thread_config(tenant_id, lead_id), entrada, ESTADO_INICIAL)


def procesar_evento_p2(graph, tenant_id: str, booking_id: str, evento: str,
                       payload: dict[str, Any] | None = None) -> dict:
    """P2: fire an operational event at the booking's thread."""
    from graphs.graph_operativo import ENTRYPOINTS_RESUME_P2, ESTADO_INICIAL_P2
    if evento not in ENTRYPOINTS_RESUME_P2:
        raise ValueError(f"evento P2 desconocido: {evento!r}")

    entrada: dict[str, Any] = {"tenant_id": tenant_id, "booking_id": booking_id,
                               "evento_entrante": evento}
    if payload:
        entrada.update(payload)
    return _invocar(graph, thread_config_p2(tenant_id, booking_id),
                    entrada, ESTADO_INICIAL_P2)


def _interrupt_pendiente(graph, config) -> dict | None:
    for task in graph.get_state(config).tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


def interrupt_pendiente(graph, tenant_id: str, lead_id: str) -> dict | None:
    """P1: pending owner approval (descuento), if any."""
    return _interrupt_pendiente(graph, thread_config(tenant_id, lead_id))


def interrupt_pendiente_p2(graph, tenant_id: str, booking_id: str) -> dict | None:
    """P2: pending owner action (proveedor sustituto), if any."""
    return _interrupt_pendiente(graph, thread_config_p2(tenant_id, booking_id))


def responder_interrupt(graph, tenant_id: str, lead_id: str, **respuesta) -> dict:
    """P1: resume a thread stopped at interrupt() with the owner's decision."""
    return graph.invoke(Command(resume=respuesta),
                        thread_config(tenant_id, lead_id))


def responder_interrupt_p2(graph, tenant_id: str, booking_id: str, **respuesta) -> dict:
    """P2: resume (p. ej. proveedor_sustituto_id=<uuid>)."""
    return graph.invoke(Command(resume=respuesta),
                        thread_config_p2(tenant_id, booking_id))
