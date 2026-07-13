"""
aiVeridia Events — Grafo comercial (P1: Convertir lead en reserva confirmada)
=============================================================================
Implementación 1:1 del BPMN P1_convertir_lead_en_reserva.bpmn.

Principios de diseño (filosofía aiVeridia):
  1. "Todo es un Runnable"        -> cada nodo es una función pura sobre el estado.
  2. "El LLM cambia, la chain no" -> router de modelos desacoplado (llm.router).
  3. "Autonomía siempre con frenos" -> interrupt() para descuentos sobre umbral;
                                     precios calculados por reglas, NUNCA por el LLM.

Los event-based gateways del BPMN (esperas con timeout) viven FUERA del grafo:
el grafo termina el turno en los nodos de espera y el checkpointer de Postgres
persiste el estado. La reanudación entra SIEMPRE por el dispatcher de START,
que enruta según `evento_entrante` (tabla ENTRYPOINTS_RESUME): webhook de
WhatsApp/pagos o EventBridge Scheduler (timeouts) solo tienen que invocar el
thread `{tenant_id}:{lead_id}` con el tipo de evento.
"""

from __future__ import annotations

import re as _re
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal, Optional

from langchain_core.messages import AnyMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from core.config import settings
from llm import prompts
from llm.router import get_llm


# ---------------------------------------------------------------------------
# 1. ESTADO TIPADO (Data Objects del BPMN)
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

    def faltantes(self) -> list[str]:
        return [k for k in ("fecha_evento", "aforo", "tipo_evento")
                if getattr(self, k) is None]


class Cotizacion(BaseModel):
    paquete_id: str
    paquete_nombre: str = ""
    incluye: list = Field(default_factory=list)
    precio_lista: float
    descuento_pct: float = 0.0
    precio_final: float
    valida_hasta: datetime


class EstadoPago(str, Enum):
    PENDIENTE = "pendiente"
    RECORDADO = "recordado"
    PAGADO = "pagado"
    VENCIDO = "vencido"


class EstadoComercial(TypedDict, total=False):
    # --- Identidad multi-tenant (SIEMPRE primero; ver RLS en schema SQL) ---
    tenant_id: str
    lead_id: str
    telefono: Optional[str]
    # --- Evento que reanuda el thread (dispatcher de START) ---
    evento_entrante: Optional[str]
    pasarela_ref: Optional[str]
    # --- Conversación ---
    messages: Annotated[list[AnyMessage], add_messages]
    # --- Data objects del proceso ---
    calificacion: Optional[Calificacion]
    fecha_disponible: Optional[bool]
    espacio_id: Optional[str]
    espacio_nombre: Optional[str]
    cotizacion: Optional[Cotizacion]
    descuento_solicitado: float
    descuento_aprobado: Optional[bool]
    estado_pago: Optional[EstadoPago]
    link_pago: Optional[str]
    booking_id: Optional[str]
    contrato_url: Optional[str]
    # --- Control de flujo (equivalente a los tokens BPMN) ---
    n_seguimientos: int
    resultado: Optional[Literal["confirmada", "no_convertido", "cancelada"]]


ESTADO_INICIAL: dict = {
    "calificacion": None,
    "descuento_solicitado": 0.0,
    "n_seguimientos": 0,
    "resultado": None,
}

MAX_SEGUIMIENTOS = 1  # BPMN: un recordatorio a las 48 h, luego 7 días -> nurturing


def _persona(state: EstadoComercial) -> str:
    return prompts.PERSONA_BASE.format(salon="el salón", ciudad="Trujillo")


# ── Extracción determinista de respaldo ─────────────────────────────────────
# El LLM extrae primero (entiende contexto); este parser rellena SOLO lo que
# el modelo dejó en blanco. Con el modelo base de dev (llama3.2) la extracción
# estructurada es flaky y esto vuelve el funnel utilizable end-to-end.
_MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
          "junio": 6, "julio": 7, "agosto": 8, "setiembre": 9, "septiembre": 9,
          "octubre": 10, "noviembre": 11, "diciembre": 12}
_TIPOS = _re.compile(r"\b(cumplea[nñ]os|baby\s*shower|bautizo|promoci[oó]n|"
                     r"quincea[nñ]er[ao]|matrimonio|boda|aniversario)\b", _re.I)
_AFORO = _re.compile(r"\b(\d{1,4})\s*(?:niñ\w*|invitad\w*|personas?|adult\w*|pax)\b", _re.I)


def _extraccion_rapida(texto: str) -> dict:
    datos: dict = {}
    t = texto.lower()

    fecha = None
    if m := _re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
        fecha = (int(m[1]), int(m[2]), int(m[3]))
    elif m := _re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", t):
        fecha = (int(m[3]), int(m[2]), int(m[1]))
    elif m := _re.search(r"\b(\d{1,2})\s+de\s+([a-záéíóúñ]+)(?:\s+(?:de|del)\s+(\d{4}))?", t):
        mes = _MESES.get(m[2])
        if mes:
            anio = int(m[3]) if m[3] else date.today().year
            fecha = (anio, mes, int(m[1]))
    if fecha:
        try:
            candidata = date(*fecha)
            if candidata >= date.today():
                datos["fecha_evento"] = candidata
        except ValueError:
            pass

    if m := _AFORO.search(t):
        datos["aforo"] = int(m[1])
    if m := _TIPOS.search(t):
        datos["tipo_evento"] = m[1].lower()
    return datos


# ---------------------------------------------------------------------------
# 2. NODOS (una función por actividad BPMN)
# ---------------------------------------------------------------------------
def a1_calificar(state: EstadoComercial) -> dict:
    """[A1/serviceTask Task_Calificar] Conversa y extrae fecha, aforo, tipo, presupuesto."""
    llm = get_llm("extraccion").with_structured_output(Calificacion)
    parcial = llm.invoke([
        SystemMessage(content=prompts.EXTRACCION.format(hoy=date.today().isoformat())),
        *state["messages"],
    ])
    previa = state.get("calificacion") or Calificacion()
    fusion = previa.model_copy(update=parcial.model_dump(exclude_none=True))

    # Respaldo determinista: rellena solo los campos que el LLM dejó en blanco
    texto_cliente = " ".join(m.content for m in state["messages"]
                             if getattr(m, "type", "") == "human")
    respaldo = {k: v for k, v in _extraccion_rapida(texto_cliente).items()
                if getattr(fusion, k) is None}
    if respaldo:
        fusion = fusion.model_copy(update=respaldo)

    from tools.crm import actualizar_calificacion
    actualizar_calificacion(state["tenant_id"], state["lead_id"],
                            fusion.model_dump(), fusion.completa())

    if not fusion.completa():
        # Falta información -> AiVeridiaEvents redacta la repregunta cálida,
        # respondiendo primero cualquier duda con el conocimiento del tenant (RAG)
        from rag.retriever import contexto_para
        ultimo = state["messages"][-1].content if state["messages"] else ""
        contexto = contexto_para(state["tenant_id"], ultimo)
        pregunta = get_llm("conversacion").invoke([
            SystemMessage(content=_persona(state) + " " +
                          prompts.REPREGUNTA.format(faltantes=fusion.faltantes()) +
                          (f"\n\n{contexto}" if contexto else "")),
            *state["messages"],
        ])
        return {"calificacion": fusion, "messages": [pregunta]}
    return {"calificacion": fusion}


def consultar_disponibilidad(state: EstadoComercial) -> dict:
    """[A1/serviceTask Task_Disponibilidad] Tool determinista contra Supabase."""
    from tools.bookings import check_availability  # query protegida por EXCLUDE
    r = check_availability(
        tenant_id=state["tenant_id"],
        fecha=state["calificacion"].fecha_evento,
        aforo=state["calificacion"].aforo,
    )
    return {"fecha_disponible": r["libre"], "espacio_id": r["espacio_id"],
            "espacio_nombre": r["espacio_nombre"]}


def proponer_alternativas(state: EstadoComercial) -> dict:
    """[A1/sendTask Task_Alternativas] Camino 'No' del GW_Disponible."""
    from tools.bookings import nearest_free_dates
    fechas = nearest_free_dates(state["tenant_id"],
                                state["calificacion"].fecha_evento,
                                state["calificacion"].aforo, n=3)
    msg = get_llm("conversacion").invoke([
        SystemMessage(content=_persona(state) + " " +
                      prompts.ALTERNATIVAS.format(fechas=fechas)),
        *state["messages"],
    ])
    return {"messages": [msg]}
    # El grafo queda en espera (Evt_Eleccion): se reanuda con el próximo webhook.


def a2_cotizar(state: EstadoComercial) -> dict:
    """[A2/businessRuleTask Task_Cotizar] Reglas deterministas — el LLM NO fija precios."""
    from tools.crm import marcar_cotizado
    from tools.pricing import cotizar, registrar_cotizacion
    q = cotizar(state["tenant_id"], state["calificacion"],
                descuento_solicitado=state.get("descuento_solicitado", 0.0))
    registrar_cotizacion(state["tenant_id"], state["lead_id"], q)
    marcar_cotizado(state["tenant_id"], state["lead_id"])
    return {"cotizacion": Cotizacion(**{k: v for k, v in q.items()
                                        if k in Cotizacion.model_fields})}


def aprobar_descuento(state: EstadoComercial) -> dict:
    """[A2/userTask Task_AprobarDescuento] Freno humano: interrupt() al dueño."""
    decision = interrupt({
        "tipo": "aprobacion_descuento",
        "tenant_id": state["tenant_id"],
        "lead_id": state["lead_id"],
        "descuento_pct": state["cotizacion"].descuento_pct,
        "precio_final": state["cotizacion"].precio_final,
    })  # push por WhatsApp al dueño; Command(resume={"aprobado": ...})
    aprobado = bool(decision.get("aprobado"))
    # Rechazado -> se re-cotiza SIN descuento (el LLM jamás lo negocia solo)
    return {"descuento_aprobado": aprobado,
            "descuento_solicitado":
                state.get("descuento_solicitado", 0.0) if aprobado else 0.0}


def enviar_cotizacion(state: EstadoComercial) -> dict:
    """[A2/sendTask Task_EnviarCotizacion] Redacción AiVeridiaEvents + PDF de paquetes."""
    q = state["cotizacion"]
    descuento_txt = (f" (incluye {q.descuento_pct:.0f}% de descuento aprobado)"
                     if q.descuento_pct else "")
    msg = get_llm("conversacion").invoke([
        SystemMessage(content=_persona(state) + " " + prompts.COTIZACION.format(
            paquete=q.paquete_nombre or q.paquete_id,
            precio=q.precio_final,
            descuento_txt=descuento_txt,
            valida_hasta=f"{q.valida_hasta:%d/%m}",
        ) + f" Incluye: {q.incluye}"),
        *state["messages"],
    ])
    from tools.timers import programar_timeout_de_espera
    programar_timeout_de_espera("enviar_cotizacion", state["tenant_id"], state["lead_id"])
    return {"messages": [msg]}
    # Espera (GW_Espera1): timeout 48 h -> Scheduler reanuda con "timeout_cotizacion"


def enviar_seguimiento(state: EstadoComercial) -> dict:
    """[A3/sendTask Task_Recordatorio] Reanudado por el Scheduler a las 48 h."""
    from tools.crm import marcar_seguimiento
    marcar_seguimiento(state["tenant_id"], state["lead_id"])
    msg = get_llm("conversacion").invoke([
        SystemMessage(content=_persona(state) + " " + prompts.SEGUIMIENTO),
        *state["messages"],
    ])
    from tools.timers import programar_timeout_de_espera
    programar_timeout_de_espera("enviar_seguimiento", state["tenant_id"], state["lead_id"])
    return {"messages": [msg], "n_seguimientos": state.get("n_seguimientos", 0) + 1}
    # Espera (GW_Espera2): timeout 7 días -> "timeout_final"


def registrar_nurturing(state: EstadoComercial) -> dict:
    """[A3/serviceTask Task_Nurturing] Fin: lead no convertido."""
    from tools.crm import to_nurturing
    to_nurturing(state["tenant_id"], state["lead_id"])
    return {"resultado": "no_convertido"}


def enviar_link_pago(state: EstadoComercial) -> dict:
    """[A4/sendTask Task_LinkPago] Crea el hold de la fecha y envía el link.

    Momento de la verdad: se RE-VERIFICA la disponibilidad aquí, no se confía
    en el estado — pudieron pasar 48 h desde la cotización, otro lead pudo
    ganar la carrera, o el thread viene del camino de alternativas con
    espacio_id=None. Si el espacio cotizado ya no está libre pero otro espacio
    del salón sí, se usa ese; solo si el día entero está tomado se ofrecen
    fechas alternativas. El hold es lo que Task_Liberar libera después.
    """
    from tools.bookings import check_availability, create_hold
    from tools.payments import crear_link_separacion

    # Evento fuera de orden (sin cotización que aceptar): el grafo no confía
    # en que el borde filtre bien — se ignora y el thread sigue esperando.
    cal = state.get("calificacion")
    if state.get("cotizacion") is None or cal is None or not cal.completa():
        return {}

    disp = check_availability(state["tenant_id"], state["calificacion"].fecha_evento,
                              state["calificacion"].aforo)
    hold = None
    if disp["libre"]:
        hold = create_hold(state["tenant_id"], state["lead_id"], disp["espacio_id"],
                           state["calificacion"].fecha_evento)
    if hold is None:  # día tomado, o carrera perdida justo ahora
        return proponer_alternativas(state) | {"fecha_disponible": False,
                                               "espacio_id": None}

    link = crear_link_separacion(state["tenant_id"], state["lead_id"],
                                 state["cotizacion"].precio_final)
    msg = get_llm("conversacion").invoke([
        SystemMessage(content=_persona(state) + " " + prompts.LINK_PAGO.format(
            link=f"{link['url']} (S/ {link['monto']:.2f} de separación)")),
        *state["messages"],
    ])
    from tools.timers import programar_timeout_de_espera
    programar_timeout_de_espera("enviar_link_pago", state["tenant_id"], state["lead_id"])
    return {"messages": [msg], "estado_pago": EstadoPago.PENDIENTE,
            "link_pago": link["url"], "booking_id": hold,
            "fecha_disponible": True, "espacio_id": disp["espacio_id"],
            "espacio_nombre": disp["espacio_nombre"]}
    # Espera (GW_EsperaPago): webhook de pasarela o timeout 24 h del Scheduler


def recordar_pago(state: EstadoComercial) -> dict:
    """[A4/sendTask Task_RecordarPago] Timeout 24 h sin pago."""
    msg = get_llm("conversacion").invoke([
        SystemMessage(content=_persona(state) + " " + prompts.RECORDATORIO_PAGO),
        *state["messages"],
    ])
    from tools.timers import programar_timeout_de_espera
    programar_timeout_de_espera("recordar_pago", state["tenant_id"], state["lead_id"])
    return {"messages": [msg], "estado_pago": EstadoPago.RECORDADO}
    # Espera (GW_EsperaPago2): webhook o timeout 48 h -> "timeout_pago_final"


def liberar_fecha(state: EstadoComercial) -> dict:
    """[A4/serviceTask Task_Liberar] Solo tras agotar la escalera de recordatorios
    (regla 5). La transición hold->cancelada queda auditada por trigger."""
    from tools.bookings import release_hold
    from tools.payments import vencer_pago
    release_hold(state["tenant_id"], state["lead_id"])
    vencer_pago(state["tenant_id"], state["lead_id"])
    return {"resultado": "cancelada", "estado_pago": EstadoPago.VENCIDO}


def registrar_reserva(state: EstadoComercial) -> dict:
    """[A4/serviceTask Task_Registrar] Bloqueo transaccional de la fecha."""
    from tools.bookings import confirm_booking
    from tools.payments import marcar_pago_recibido
    marcar_pago_recibido(state["tenant_id"], state["lead_id"],
                         state.get("pasarela_ref") or "manual")
    booking_id = confirm_booking(state["tenant_id"], state["lead_id"])
    return {"booking_id": booking_id, "estado_pago": EstadoPago.PAGADO}


def generar_contrato_y_confirmar(state: EstadoComercial) -> dict:
    """[A4] Task_Contrato + Task_EnviarConf + End_Confirmada (message end):
    publica el evento que dispara el grafo operativo P2."""
    from tools.contracts import generar_contrato_pdf
    from tools.events import publish
    url = generar_contrato_pdf(state["tenant_id"], state["booking_id"])
    msg = get_llm("conversacion").invoke([
        SystemMessage(content=_persona(state) + " " +
                      prompts.CONFIRMACION.format(contrato=url)),
        *state["messages"],
    ])
    publish("reserva.confirmada", tenant_id=state["tenant_id"],
            booking_id=state["booking_id"])           # -> Start_Reserva del grafo P2
    return {"messages": [msg], "resultado": "confirmada", "contrato_url": url}


# ---------------------------------------------------------------------------
# 3. ROUTERS (gateways BPMN — solo encaminan, no deciden)
# ---------------------------------------------------------------------------
# Los event-based gateways (GW_Espera1/2, GW_EsperaPago1/2) viven FUERA del
# grafo: cada espera termina el turno (END) y el evento externo reanuda el
# thread entrando por el dispatcher de START con `evento_entrante`.
ENTRYPOINTS_RESUME = {
    "mensaje_cliente":    "a1_calificar",
    "cliente_acepta":     "enviar_link_pago",
    "timeout_cotizacion": "enviar_seguimiento",
    "timeout_final":      "registrar_nurturing",
    "pago_ok":            "registrar_reserva",
    "timeout_pago":       "recordar_pago",
    "timeout_pago_final": "liberar_fecha",
}


def dispatch_evento(state: EstadoComercial) -> str:
    evento = state.get("evento_entrante") or "mensaje_cliente"
    return ENTRYPOINTS_RESUME.get(evento, "a1_calificar")


def gw_calificacion(state: EstadoComercial) -> str:
    return "consultar_disponibilidad" if (
        state.get("calificacion") and state["calificacion"].completa()
    ) else END  # espera la respuesta del cliente (repregunta ya enviada)


def gw_disponible(state: EstadoComercial) -> str:
    return "a2_cotizar" if state["fecha_disponible"] else "proponer_alternativas"


def gw_descuento(state: EstadoComercial) -> str:
    return ("aprobar_descuento"
            if state["cotizacion"].descuento_pct > settings.aiv_umbral_descuento
            else "enviar_cotizacion")


def gw_post_aprobacion(state: EstadoComercial) -> str:
    return "enviar_cotizacion" if state["descuento_aprobado"] else "a2_cotizar"


# ---------------------------------------------------------------------------
# 4. ENSAMBLAJE DEL GRAFO
# ---------------------------------------------------------------------------
def build_graph(checkpointer):
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

    g.add_conditional_edges(START, dispatch_evento,
                            sorted(set(ENTRYPOINTS_RESUME.values())))
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
