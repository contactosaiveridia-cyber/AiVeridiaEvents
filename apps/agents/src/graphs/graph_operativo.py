"""
aiVeridia Events — Grafo operativo (P2: Ejecutar y fidelizar evento confirmado)
===============================================================================
Implementación 1:1 del BPMN P2_ejecutar_y_fidelizar_evento.bpmn, con el mismo
patrón que P1: las esperas y timers viven FUERA del grafo; cada evento externo
entra por el dispatcher de START (`evento_entrante`) y el checkpointer persiste
el estado entre turnos. thread_id = "{tenant_id}:{booking_id}:p2".

Interpretaciones adoptadas (ver docs/lectura_bpmn.md, puntos 5-7):
  - Multi-instancia de proveedores: una rama de estado por proveedor con su
    boundary timer propio (evento "timeout_proveedor:{proveedor_id}", 48 h).
    La escalación al dueño (userTask) es un interrupt() por instancia.
  - GW_Join (proveedores + cobranza): el chequeo de saldo y el checklist se
    disparan por el timer absoluto D-7 (checkpoint_d7), programado al iniciar.
  - Los timers absolutos (D-7, día del evento, +1 día, +10 meses) se programan
    con EventBridge Scheduler (APScheduler en dev) sobre la fecha del evento.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Annotated, Literal, Optional

from langchain_core.messages import AnyMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from typing_extensions import TypedDict

from llm import prompts
from llm.router import get_llm

TIMEOUT_PROVEEDOR_H = 48


class EstadoOperativo(TypedDict, total=False):
    tenant_id: str
    booking_id: str
    lead_id: Optional[str]
    telefono: Optional[str]
    agasajado: Optional[str]
    fecha_evento: Optional[str]              # ISO date
    # branding por tenant (regla 3: nada del salón se hardcodea en código)
    salon: Optional[str]
    ciudad: Optional[str]
    link_resena: Optional[str]
    evento_entrante: Optional[str]
    # multi-instancia A5: proveedor_id -> notificado|confirmado|escalado|sustituido
    ordenes: dict[str, str]
    proveedor_id: Optional[str]              # payload del evento en curso
    # cobranza A7
    cronograma: Optional[dict]
    saldo_pendiente: Optional[float]
    pago_ref: Optional[str]
    pago_monto: Optional[float]
    # post-evento A8
    nps_score: Optional[int]
    nps_comentario: Optional[str]
    messages: Annotated[list[AnyMessage], add_messages]
    resultado: Optional[Literal["ciclo_completado"]]


ESTADO_INICIAL_P2: dict = {"ordenes": {}, "resultado": None}


def _fecha(state: EstadoOperativo) -> datetime:
    from datetime import date
    d = date.fromisoformat(state["fecha_evento"])
    return datetime.combine(d, time(12, 0), tzinfo=timezone.utc)


def _persona(state: EstadoOperativo) -> str:
    return prompts.PERSONA_BASE.format(salon=state.get("salon") or "el salón",
                                       ciudad=state.get("ciudad") or "Perú")


# ---------------------------------------------------------------------------
# NODOS
# ---------------------------------------------------------------------------
def iniciar_operacion(state: EstadoOperativo) -> dict:
    """[Start_Reserva + GW_Split] Carga el contexto de la reserva; el split
    paralelo son las dos edges salientes (proveedores ∥ cronograma)."""
    from tools.bookings import datos_reserva
    datos = datos_reserva(state["tenant_id"], state["booking_id"])
    return {"lead_id": datos["lead_id"], "telefono": datos["telefono"],
            "agasajado": datos["agasajado"],
            "fecha_evento": str(datos["fecha_evento"]),
            "salon": datos.get("salon"), "ciudad": datos.get("ciudad"),
            "link_resena": datos.get("link_resena")}


def notificar_proveedores(state: EstadoOperativo) -> dict:
    """[A5/sendTask Task_NotificarProv, multi-instancia] Una orden por rubro,
    con boundary timer de 48 h por proveedor."""
    from tools.channels import get_channel
    from tools.proveedores import crear_ordenes
    from tools.timers import programar_en

    ordenes = crear_ordenes(state["tenant_id"], state["booking_id"])
    canal = get_channel()
    vence = datetime.now(timezone.utc) + timedelta(hours=TIMEOUT_PROVEEDOR_H)
    estado_ordenes = dict(state.get("ordenes", {}))

    for orden in ordenes:
        canal.send_text(state["tenant_id"], orden["telefono"] or "",
                        prompts.ORDEN_PROVEEDOR.format(
                            salon=state.get("salon") or "el salón",
                            fecha=state["fecha_evento"],
                            rubro=orden["rubro"], detalle=orden["nombre"]))
        programar_en(state["tenant_id"], state["booking_id"],
                     f"timeout_proveedor:{orden['proveedor_id']}", vence)
        estado_ordenes[orden["proveedor_id"]] = "notificado"

    return {"ordenes": estado_ordenes}
    # Espera Task_RecibirConf: cada confirmación llega como "proveedor_confirma"


def programar_cronograma(state: EstadoOperativo) -> dict:
    """[A7/serviceTask Task_Cronograma] Cuotas deterministas + timer D-7."""
    from tools.cobranza import generar_cronograma
    from tools.timers import programar_en

    plan = generar_cronograma(state["tenant_id"], state["booking_id"])
    programar_en(state["tenant_id"], state["booking_id"], "checkpoint_d7",
                 _fecha(state) - timedelta(days=7))
    return {"cronograma": plan, "saldo_pendiente": plan["saldo"]}


def registrar_confirmacion_proveedor(state: EstadoOperativo) -> dict:
    """[A5/receiveTask Task_RecibirConf] Confirmación de una instancia: cancela
    su boundary timer."""
    from tools.proveedores import marcar_confirmado
    from tools.timers import cancelar

    proveedor_id = state["proveedor_id"]
    marcar_confirmado(state["tenant_id"], state["booking_id"], proveedor_id)
    cancelar(state["tenant_id"], state["booking_id"],
             f"timeout_proveedor:{proveedor_id}")
    return {"ordenes": {**state.get("ordenes", {}), proveedor_id: "confirmado"}}


def escalar_proveedor(state: EstadoOperativo) -> dict:
    """[A5/Boundary_TimerProv -> userTask Task_Escalar] 48 h sin confirmar:
    interrupt() al dueño para gestionar el sustituto."""
    from tools.proveedores import marcar_escalado, marcar_sustituido

    proveedor_id = state["proveedor_id"]
    marcar_escalado(state["tenant_id"], state["booking_id"], proveedor_id)
    decision = interrupt({
        "tipo": "proveedor_sin_confirmar",
        "tenant_id": state["tenant_id"],
        "booking_id": state["booking_id"],
        "proveedor_id": proveedor_id,
        "fecha_evento": state["fecha_evento"],
    })  # el dueño responde {"proveedor_sustituto_id": <uuid> | None}
    sustituto = decision.get("proveedor_sustituto_id")
    marcar_sustituido(state["tenant_id"], state["booking_id"],
                      proveedor_id, sustituto)
    return {"ordenes": {**state.get("ordenes", {}), proveedor_id: "sustituido"}}


def conciliar_pagos(state: EstadoOperativo) -> dict:
    """[A7/serviceTask Task_Conciliar] Yape/Plin/pasarela -> cuota conciliada
    contra el monto realmente recibido (sin monto = conciliación manual)."""
    from tools.cobranza import registrar_pago_cuota
    saldo = registrar_pago_cuota(state["tenant_id"], state["booking_id"],
                                 state.get("pago_ref") or "manual",
                                 monto=state.get("pago_monto"))
    return {"saldo_pendiente": saldo}


def checklist_pre_evento(state: EstadoOperativo) -> dict:
    """[GW_Saldo + Task_NotifSaldo + Evt_TimerPre + Task_Checklist] D-7:
    saldo pendiente -> notificación (cliente y dueño); luego checklist con las
    reglas del local (RAG del tenant); y se programa el día del evento."""
    from rag.retriever import contexto_para
    from tools.cobranza import saldo_pendiente
    from tools.timers import programar_en

    mensajes = []
    saldo = saldo_pendiente(state["tenant_id"], state["booking_id"])
    if saldo > 0:
        aviso = get_llm("conversacion").invoke([
            SystemMessage(content=_persona(state) + " " +
                          prompts.NOTIF_SALDO.format(saldo=saldo))])
        mensajes.append(aviso)

    contexto = contexto_para(state["tenant_id"], "reglas del local y montaje")
    checklist = get_llm("conversacion").invoke([
        SystemMessage(content=_persona(state) + " " + prompts.CHECKLIST.format(
            contexto=f"\n\n{contexto}" if contexto else ""))])
    mensajes.append(checklist)

    programar_en(state["tenant_id"], state["booking_id"], "dia_evento",
                 _fecha(state).replace(hour=8))
    return {"messages": mensajes, "saldo_pendiente": saldo}


def coordinar_montaje(state: EstadoOperativo) -> dict:
    """[A6/userTask Task_Montaje] Día del evento: coordinación con el dueño
    (guion de montaje/acceso) y timer del post-evento (+1 día)."""
    from tools.timers import programar_en

    msg = get_llm("conversacion").invoke([
        SystemMessage(content=_persona(state) +
                      " Hoy es el evento. Escribe el mensaje de coordinación de "
                      "montaje para el cliente: hora de acceso (2 h antes), "
                      "contacto del salón y buenos deseos. Breve.")])
    programar_en(state["tenant_id"], state["booking_id"], "post_evento",
                 _fecha(state) + timedelta(days=1))
    return {"messages": [msg]}


def enviar_nps(state: EstadoOperativo) -> dict:
    """[A8/sendTask Task_NPS] +1 día: encuesta NPS + reseña en Google."""
    from tools.fidelizacion import marcar_ejecutada
    marcar_ejecutada(state["tenant_id"], state["booking_id"])
    msg = get_llm("conversacion").invoke([
        SystemMessage(content=_persona(state) + " " + prompts.NPS.format(
            link_resena=state.get("link_resena") or "nuestro perfil de Google"))])
    return {"messages": [msg]}
    # Espera: la respuesta llega como "nps_respuesta" (score en payload)


def registrar_nps(state: EstadoOperativo) -> dict:
    """[Task_NPS respuesta] Guarda score y comentario."""
    from tools.fidelizacion import guardar_nps
    guardar_nps(state["tenant_id"], state["booking_id"],
                state["nps_score"], state.get("nps_comentario"))
    return {}


def actualizar_metricas(state: EstadoOperativo) -> dict:
    """[A9/serviceTask Task_Metricas] Refresca el dashboard del dueño y
    programa la campaña de renovación (+10 meses)."""
    from tools.fidelizacion import crear_campania, refresh_metricas
    from tools.timers import programar_en

    refresh_metricas()
    disparo = _fecha(state) + timedelta(days=300)   # ~10 meses
    crear_campania(state["tenant_id"], state["booking_id"],
                   state.get("agasajado"), disparo)
    programar_en(state["tenant_id"], state["booking_id"],
                 "campania_renovacion", disparo)
    return {}


def enviar_campania(state: EstadoOperativo) -> dict:
    """[A8/sendTask Task_Campania + End_Ciclo] +10 meses: próximo cumpleaños
    con fecha pre-reservada. Cierra el ciclo y activa la recurrencia anual."""
    from tools.fidelizacion import marcar_campania_enviada
    msg = get_llm("conversacion").invoke([
        SystemMessage(content=_persona(state) + " " + prompts.CAMPANIA.format(
            agasajado=state.get("agasajado") or "su engreído(a)",
            salon=state.get("salon") or "el salón"))])
    marcar_campania_enviada(state["tenant_id"], state["booking_id"])
    return {"messages": [msg], "resultado": "ciclo_completado"}


# ---------------------------------------------------------------------------
# DISPATCHER Y ENSAMBLAJE
# ---------------------------------------------------------------------------
ENTRYPOINTS_RESUME_P2 = {
    "reserva_confirmada":   "iniciar_operacion",
    "proveedor_confirma":   "registrar_confirmacion_proveedor",
    "timeout_proveedor":    "escalar_proveedor",
    "pago_cuota":           "conciliar_pagos",
    "checkpoint_d7":        "checklist_pre_evento",
    "dia_evento":           "coordinar_montaje",
    "post_evento":          "enviar_nps",
    "nps_respuesta":        "registrar_nps",
    "campania_renovacion":  "enviar_campania",
}


def dispatch_evento(state: EstadoOperativo) -> str:
    return ENTRYPOINTS_RESUME_P2[state["evento_entrante"]]


def build_graph_operativo(checkpointer):
    g = StateGraph(EstadoOperativo)

    g.add_node("iniciar_operacion", iniciar_operacion)
    g.add_node("notificar_proveedores", notificar_proveedores)
    g.add_node("programar_cronograma", programar_cronograma)
    g.add_node("registrar_confirmacion_proveedor", registrar_confirmacion_proveedor)
    g.add_node("escalar_proveedor", escalar_proveedor)
    g.add_node("conciliar_pagos", conciliar_pagos)
    g.add_node("checklist_pre_evento", checklist_pre_evento)
    g.add_node("coordinar_montaje", coordinar_montaje)
    g.add_node("enviar_nps", enviar_nps)
    g.add_node("registrar_nps", registrar_nps)
    g.add_node("actualizar_metricas", actualizar_metricas)
    g.add_node("enviar_campania", enviar_campania)

    g.add_conditional_edges(START, dispatch_evento,
                            sorted(set(ENTRYPOINTS_RESUME_P2.values())))
    # GW_Split paralelo: proveedores ∥ cronograma
    g.add_edge("iniciar_operacion", "notificar_proveedores")
    g.add_edge("iniciar_operacion", "programar_cronograma")
    g.add_edge("notificar_proveedores", END)          # espera confirmaciones
    g.add_edge("programar_cronograma", END)           # espera cuotas / D-7
    g.add_edge("registrar_confirmacion_proveedor", END)
    g.add_edge("escalar_proveedor", END)
    g.add_edge("conciliar_pagos", END)
    g.add_edge("checklist_pre_evento", END)           # espera día del evento
    g.add_edge("coordinar_montaje", END)              # espera +1 día
    g.add_edge("enviar_nps", END)                     # espera respuesta NPS
    g.add_edge("registrar_nps", "actualizar_metricas")
    g.add_edge("actualizar_metricas", END)            # espera +10 meses
    g.add_edge("enviar_campania", END)                # End_Ciclo

    return g.compile(checkpointer=checkpointer)
