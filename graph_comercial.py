"""
aiVeridia Events — Grafo comercial (P1: Convertir lead en reserva confirmada)
=============================================================================
Implementación 1:1 del BPMN P1_convertir_lead_en_reserva.bpmn.

Principios de diseño (filosofía aiVeridia):
  1. "Todo es un Runnable"        -> cada nodo es una función pura sobre el estado.
  2. "El LLM cambia, la chain no" -> router de modelos desacoplado (AiVeridiaEvents
                                     en Bedrock CMI / Ollama local, fallback Gemini).
  3. "Autonomía siempre con frenos" -> interrupt() para descuentos sobre umbral;
                                     precios calculados por reglas, NUNCA por el LLM.

Los event-based gateways del BPMN (esperas con timeout) se implementan con el
checkpointer de Postgres: el grafo se pausa en nodos de espera y se reanuda por
webhook (mensaje del cliente / pago) o por EventBridge Scheduler (timeout).
"""

from __future__ import annotations

import os
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal, Optional

from langchain_core.messages import AnyMessage, SystemMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

# ---------------------------------------------------------------------------
# 1. ROUTER DE MODELOS — AiVeridiaEvents como LLM principal
# ---------------------------------------------------------------------------
# AiVeridiaEvents = Llama 3.1 8B Instruct + QLoRA fine-tuned con:
#   - transcripciones reales de ventas de salones (caso Los Jazmines),
#   - cotizaciones históricas y objeciones frecuentes,
#   - registro peruano coloquial-comercial (voseo cero, "señito", Yape/Plin).
# Producción: Bedrock Custom Model Import (serverless, pago por token).
# Desarrollo:  Ollama local (misma plantilla de chat, mismos adapters fusionados).

def get_llm(task: Literal["conversacion", "extraccion", "razonamiento"]):
    """El LLM cambia, la chain no: un solo punto de decisión de modelo."""
    env = os.getenv("AIV_ENV", "dev")
    if task in ("conversacion", "extraccion"):
        if env == "prod":
            from langchain_aws import ChatBedrockConverse
            return ChatBedrockConverse(
                model=os.environ["AIVERIDIA_EVENTS_MODEL_ARN"],  # Bedrock CMI
                temperature=0.3 if task == "conversacion" else 0.0,
            )
        from langchain_ollama import ChatOllama
        return ChatOllama(model="aiveridia-events:8b", temperature=0.3)
    # Escalación de razonamiento complejo (negociaciones atípicas, quejas):
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2)


# ---------------------------------------------------------------------------
# 2. ESTADO TIPADO (Data Objects del BPMN)
# ---------------------------------------------------------------------------
class Calificacion(BaseModel):
    fecha_evento: Optional[date] = None
    aforo: Optional[int] = None
    tipo_evento: Optional[str] = Field(None, description="cumpleaños, baby shower, ...")
    presupuesto_max: Optional[float] = None
    nombre_contacto: Optional[str] = None
    nombre_agasajado: Optional[str] = None   # clave para la campaña anual (A8)

    def completa(self) -> bool:
        return all([self.fecha_evento, self.aforo, self.tipo_evento])


class Cotizacion(BaseModel):
    paquete_id: str
    precio_lista: float
    descuento_pct: float = 0.0
    precio_final: float
    valida_hasta: datetime


class EstadoPago(str, Enum):
    PENDIENTE = "pendiente"
    RECORDADO = "recordado"
    PAGADO = "pagado"
    VENCIDO = "vencido"


class EstadoComercial(TypedDict):
    # --- Identidad multi-tenant (SIEMPRE primero; ver RLS en schema SQL) ---
    tenant_id: str
    lead_id: str
    # --- Conversación ---
    messages: Annotated[list[AnyMessage], add_messages]
    # --- Data objects del proceso ---
    calificacion: Optional[Calificacion]
    fecha_disponible: Optional[bool]
    cotizacion: Optional[Cotizacion]
    descuento_aprobado: Optional[bool]
    estado_pago: EstadoPago
    booking_id: Optional[str]
    # --- Control de flujo (equivalente a los tokens BPMN) ---
    n_seguimientos: int
    resultado: Optional[Literal["confirmada", "no_convertido", "cancelada"]]


UMBRAL_DESCUENTO_PCT = float(os.getenv("AIV_UMBRAL_DESCUENTO", "10"))
MAX_SEGUIMIENTOS = 1  # BPMN: un recordatorio a las 48 h, luego 7 días -> nurturing


# ---------------------------------------------------------------------------
# 3. NODOS (una función por actividad BPMN)
# ---------------------------------------------------------------------------
def a1_calificar(state: EstadoComercial) -> dict:
    """[A1/serviceTask Task_Calificar] Conversa y extrae fecha, aforo, tipo, presupuesto."""
    llm = get_llm("extraccion").with_structured_output(Calificacion)
    system = SystemMessage(content=(
        "Eres el asistente comercial de un salón de eventos infantiles en Perú. "
        "Extrae los datos del evento de la conversación. No inventes valores."
    ))
    parcial = llm.invoke([system, *state["messages"]])
    previa = state.get("calificacion") or Calificacion()
    fusion = previa.model_copy(update=parcial.model_dump(exclude_none=True))

    if not fusion.completa():
        # Falta información -> AiVeridiaEvents redacta la repregunta cálida
        pregunta = get_llm("conversacion").invoke([
            SystemMessage(content=(
                "Pide amablemente SOLO el dato faltante para cotizar "
                f"(faltan: {[k for k, v in fusion.model_dump().items() if v is None]}). "
                "Tono peruano cercano, máximo 2 líneas, un emoji."
            )),
            *state["messages"],
        ])
        return {"calificacion": fusion, "messages": [pregunta]}
    return {"calificacion": fusion}


def consultar_disponibilidad(state: EstadoComercial) -> dict:
    """[A1/serviceTask Task_Disponibilidad] Tool determinista contra Supabase."""
    from tools.bookings import check_availability  # SELECT con constraint EXCLUDE
    libre = check_availability(
        tenant_id=state["tenant_id"],
        fecha=state["calificacion"].fecha_evento,
        aforo=state["calificacion"].aforo,
    )
    return {"fecha_disponible": libre}


def proponer_alternativas(state: EstadoComercial) -> dict:
    """[A1/sendTask Task_Alternativas] Camino 'No' del GW_Disponible."""
    from tools.bookings import nearest_free_dates
    fechas = nearest_free_dates(state["tenant_id"],
                                state["calificacion"].fecha_evento, n=3)
    msg = get_llm("conversacion").invoke([
        SystemMessage(content=(
            f"La fecha pedida está ocupada. Ofrece estas alternativas: {fechas}. "
            "Genera urgencia real (los sábados vuelan) sin presionar."
        )),
        *state["messages"],
    ])
    return {"messages": [msg]}
    # El grafo queda en espera (Evt_Eleccion): se reanuda con el próximo webhook.


def a2_cotizar(state: EstadoComercial) -> dict:
    """[A2/businessRuleTask Task_Cotizar] Reglas deterministas — el LLM NO fija precios."""
    from tools.pricing import cotizar  # temporada, día de semana, aforo, paquete
    q = cotizar(state["tenant_id"], state["calificacion"])
    return {"cotizacion": Cotizacion(**q)}


def aprobar_descuento(state: EstadoComercial) -> dict:
    """[A2/userTask Task_AprobarDescuento] Freno humano: interrupt() al dueño."""
    decision = interrupt({
        "tipo": "aprobacion_descuento",
        "tenant_id": state["tenant_id"],
        "lead_id": state["lead_id"],
        "descuento_pct": state["cotizacion"].descuento_pct,
        "precio_final": state["cotizacion"].precio_final,
    })  # push por WhatsApp al dueño; Command(resume=...) con su respuesta
    return {"descuento_aprobado": bool(decision.get("aprobado"))}


def enviar_cotizacion(state: EstadoComercial) -> dict:
    """[A2/sendTask Task_EnviarCotizacion] Redacción AiVeridiaEvents + PDF de paquetes."""
    q = state["cotizacion"]
    msg = get_llm("conversacion").invoke([
        SystemMessage(content=(
            f"Presenta la cotización: paquete {q.paquete_id}, S/ {q.precio_final:.2f} "
            f"(válida hasta {q.valida_hasta:%d/%m}). Incluye 3 bullets de valor y "
            "cierra preguntando si separamos la fecha. Tono cálido peruano."
        )),
        *state["messages"],
    ])
    # side-effect: adjuntar PDF/fotos vía WhatsApp API (tools.channels.send_media)
    return {"messages": [msg]}
    # Espera (GW_Espera1): timeout 48 h lo dispara EventBridge -> resume "timeout"


def enviar_seguimiento(state: EstadoComercial) -> dict:
    """[A3/sendTask Task_Recordatorio] Reanudado por el Scheduler a las 48 h."""
    msg = get_llm("conversacion").invoke([
        SystemMessage(content=(
            "El cliente no respondió en 48 h. Redacta UN seguimiento breve y útil "
            "(no rogar): recuerda disponibilidad y ofrece resolver dudas."
        )),
        *state["messages"],
    ])
    return {"messages": [msg], "n_seguimientos": state["n_seguimientos"] + 1}


def registrar_nurturing(state: EstadoComercial) -> dict:
    """[A3/serviceTask Task_Nurturing] Fin: lead no convertido."""
    from tools.crm import to_nurturing
    to_nurturing(state["tenant_id"], state["lead_id"])
    return {"resultado": "no_convertido"}


def enviar_link_pago(state: EstadoComercial) -> dict:
    """[A4/sendTask Task_LinkPago] Genera link (Culqi/MercadoPago/Yape) y lo envía."""
    from tools.payments import crear_link_separacion
    link = crear_link_separacion(state["tenant_id"], state["lead_id"],
                                 state["cotizacion"].precio_final)
    msg = get_llm("conversacion").invoke([
        SystemMessage(content=(
            f"El cliente aceptó. Envía el link de separación ({link}) explicando que "
            "la fecha se bloquea al confirmarse el pago. Tono de celebración contenida."
        )),
        *state["messages"],
    ])
    return {"messages": [msg], "estado_pago": EstadoPago.PENDIENTE}
    # Espera (GW_EsperaPago): webhook de pasarela o timeout 24 h del Scheduler


def recordar_pago(state: EstadoComercial) -> dict:
    """[A4/sendTask Task_RecordarPago] Timeout 24 h sin pago."""
    msg = get_llm("conversacion").invoke([
        SystemMessage(content="Recuerda con amabilidad que el link de separación "
                              "sigue activo y la fecha aún está reservada a su nombre."),
        *state["messages"],
    ])
    return {"messages": [msg], "estado_pago": EstadoPago.RECORDADO}


def liberar_fecha(state: EstadoComercial) -> dict:
    """[A4/serviceTask Task_Liberar] Timeout final 48 h: fin 'cancelada'."""
    from tools.bookings import release_hold
    release_hold(state["tenant_id"], state["lead_id"])
    return {"resultado": "cancelada", "estado_pago": EstadoPago.VENCIDO}


def registrar_reserva(state: EstadoComercial) -> dict:
    """[A4/serviceTask Task_Registrar] Bloqueo transaccional de la fecha."""
    from tools.bookings import confirm_booking
    booking_id = confirm_booking(state["tenant_id"], state["lead_id"],
                                 state["calificacion"], state["cotizacion"])
    return {"booking_id": booking_id, "estado_pago": EstadoPago.PAGADO}


def generar_contrato_y_confirmar(state: EstadoComercial) -> dict:
    """[A4] Task_Contrato + Task_EnviarConf + End_Confirmada (message end):
    publica el evento que dispara el grafo operativo P2."""
    from tools.contracts import generar_contrato_pdf
    from tools.events import publish
    url = generar_contrato_pdf(state["tenant_id"], state["booking_id"])
    msg = get_llm("conversacion").invoke([
        SystemMessage(content=(
            f"Confirma la reserva, adjunta el contrato ({url}) y resume próximos "
            "pasos. Cierra con entusiasmo genuino por la fiesta."
        )),
        *state["messages"],
    ])
    publish("reserva.confirmada", tenant_id=state["tenant_id"],
            booking_id=state["booking_id"])           # -> Start_Reserva del grafo P2
    return {"messages": [msg], "resultado": "confirmada"}


# ---------------------------------------------------------------------------
# 4. ROUTERS (gateways BPMN — solo encaminan, no deciden)
# ---------------------------------------------------------------------------
def gw_calificacion(state: EstadoComercial) -> str:
    return "consultar_disponibilidad" if (
        state.get("calificacion") and state["calificacion"].completa()
    ) else END  # espera la respuesta del cliente (repregunta ya enviada)


def gw_disponible(state: EstadoComercial) -> str:
    return "a2_cotizar" if state["fecha_disponible"] else "proponer_alternativas"


def gw_descuento(state: EstadoComercial) -> str:
    return ("aprobar_descuento"
            if state["cotizacion"].descuento_pct > UMBRAL_DESCUENTO_PCT
            else "enviar_cotizacion")


def gw_post_aprobacion(state: EstadoComercial) -> str:
    return "enviar_cotizacion" if state["descuento_aprobado"] else "a2_cotizar"


# Los event-based gateways (GW_Espera1/2, GW_EsperaPago1/2) viven FUERA del grafo:
# el webhook/Scheduler reanuda el thread invocando el nodo destino correcto según
# el evento recibido ("cliente_acepta" | "timeout_48h" | "pago_ok" | ...).
ENTRYPOINTS_RESUME = {
    "mensaje_cliente":   "a1_calificar",
    "cliente_acepta":    "enviar_link_pago",
    "timeout_cotizacion": "enviar_seguimiento",
    "timeout_final":     "registrar_nurturing",
    "pago_ok":           "registrar_reserva",
    "timeout_pago":      "recordar_pago",
    "timeout_pago_final": "liberar_fecha",
}

# ---------------------------------------------------------------------------
# 5. ENSAMBLAJE DEL GRAFO
# ---------------------------------------------------------------------------
def build_graph(checkpointer: PostgresSaver):
    g = StateGraph(EstadoComercial)

    g.add_node("a1_calificar", a1_calificar)
    g.add_node("consultar_disponibilidad", consultar_disponibilidad)
    g.add_node("proponer_alternativas", proponer_alternativas)
    g.add_node("a2_cotizar", a2_cotizar)
    g.add_node("aprobar_descuento", aprobar_descuento)
    g.add_node("enviar_cotizacion", enviar_cotizacion)
    g.add_node("enviar_seguimiento", enviar_seguimiento)
    g.add_node("registrar_nurturing", registrar_nurturing)
    g.add_node("enviar_link_pago", enviar_link_pago)
    g.add_node("recordar_pago", recordar_pago)
    g.add_node("liberar_fecha", liberar_fecha)
    g.add_node("registrar_reserva", registrar_reserva)
    g.add_node("generar_contrato_y_confirmar", generar_contrato_y_confirmar)

    g.add_edge(START, "a1_calificar")
    g.add_conditional_edges("a1_calificar", gw_calificacion,
                            ["consultar_disponibilidad", END])
    g.add_conditional_edges("consultar_disponibilidad", gw_disponible,
                            ["a2_cotizar", "proponer_alternativas"])
    g.add_edge("proponer_alternativas", END)          # espera elección (Evt_Eleccion)
    g.add_conditional_edges("a2_cotizar", gw_descuento,
                            ["aprobar_descuento", "enviar_cotizacion"])
    g.add_conditional_edges("aprobar_descuento", gw_post_aprobacion,
                            ["enviar_cotizacion", "a2_cotizar"])
    g.add_edge("enviar_cotizacion", END)              # espera GW_Espera1
    g.add_edge("enviar_seguimiento", END)             # espera GW_Espera2
    g.add_edge("registrar_nurturing", END)            # End_NoConvertido
    g.add_edge("enviar_link_pago", END)               # espera GW_EsperaPago
    g.add_edge("recordar_pago", END)                  # espera GW_EsperaPago2
    g.add_edge("liberar_fecha", END)                  # End_Cancelada
    g.add_edge("registrar_reserva", "generar_contrato_y_confirmar")
    g.add_edge("generar_contrato_y_confirmar", END)   # End_Confirmada

    return g.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# 6. INVOCACIÓN (FastAPI webhook / EventBridge resume)
# ---------------------------------------------------------------------------
# thread_id = f"{tenant_id}:{lead_id}"  -> aislamiento por tenant también en
# los checkpoints. El webhook de WhatsApp y el Scheduler llaman:
#
#   graph.update_state(config, values, as_node=<nodo previo>)   # inyecta evento
#   graph.invoke(None, config)                                   # reanuda
#
# con config = {"configurable": {"thread_id": thread_id,
#                                "tenant_id": tenant_id}}
