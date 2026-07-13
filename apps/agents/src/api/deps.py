"""Shared edge logic: fire an event at a thread and deliver the reply.

TODO el borde pasa por aquí — los routers FastAPI (api/webhooks_*.py), el
entrypoint Bedrock AgentCore (agentcore/app_comercial.py) y el backend de
timers — para que dedup, clasificación de aceptación, entrega por canal y
bandeja del dueño existan exactamente una vez.
"""

import hashlib
import hmac
import json
import logging
import re

from core.config import settings
from graphs import runtime
from tools.channels import get_channel

log = logging.getLogger("aiveridia.api")

# ── Evt_Acepta (heurística MVP; en F6 pasa a clasificación del modelo) ──────
_ACEPTA = re.compile(
    r"\b(s[ií]|acepto|de acuerdo|dale|listo|ok(ey)?|separa\w*|reserv\w*|"
    r"confirmo|lo tomo|me quedo)\b", re.IGNORECASE)
# Negaciones y aplazamientos anulan el match ("no acepto", "ok, lo consulto")
_RECHAZO = re.compile(
    r"\b(no|nunca|jam[aá]s|tampoco|todav[ií]a|a[uú]n|luego|despu[eé]s|"
    r"avis\w*|pensar\w*|pienso|consult\w*|pregunt\w*)\b",
    re.IGNORECASE)


def es_aceptacion(texto: str, estado: dict) -> bool:
    """True solo si hay cotización vigente esperando respuesta y el texto es
    una afirmación clara SIN negación/aplazamiento (GW_Espera1/2 -> Evt_Acepta)."""
    esperando = estado.get("cotizacion") is not None and estado.get("estado_pago") is None
    if not esperando or not texto:
        return False
    return bool(_ACEPTA.search(texto)) and not _RECHAZO.search(texto)


# ── Despacho P1 ──────────────────────────────────────────────────────────────
def despachar_evento(graph, tenant_id: str, lead_id: str, evento: str,
                     texto: str | None = None, payload: dict | None = None,
                     telefono: str | None = None) -> dict:
    config = runtime.thread_config(tenant_id, lead_id)
    n_previos = len(graph.get_state(config).values.get("messages", []))

    if telefono:  # persistir en el estado: pago_ok y los timeouts no lo traen
        payload = {**(payload or {}), "telefono": telefono}

    resultado = runtime.procesar_evento(graph, tenant_id, lead_id, evento,
                                        texto=texto, payload=payload)

    # Entregar por el canal SOLO lo nuevo de este turno; sin telefono explícito
    # (webhook de pagos, Scheduler) se usa el guardado en el estado del thread.
    destino = telefono or resultado.get("telefono")
    mensajes = resultado.get("messages", [])
    if destino and len(mensajes) > n_previos and mensajes[-1].type == "ai":
        get_channel().send_text(tenant_id, destino, mensajes[-1].content)

    pendiente = runtime.interrupt_pendiente(graph, tenant_id, lead_id)
    if pendiente:
        _a_bandeja(tenant_id, pendiente, referencia=lead_id)
    return resultado


# ── Despacho P2 ──────────────────────────────────────────────────────────────
def despachar_evento_p2(graph_p2, tenant_id: str, booking_id: str, evento: str,
                        payload: dict | None = None) -> dict:
    config = runtime.thread_config_p2(tenant_id, booking_id)
    n_previos = len(graph_p2.get_state(config).values.get("messages", []))

    resultado = runtime.procesar_evento_p2(graph_p2, tenant_id, booking_id,
                                           evento, payload=payload)

    mensajes = resultado.get("messages", [])
    telefono = resultado.get("telefono")
    if telefono and len(mensajes) > n_previos:
        canal = get_channel()
        for msg in mensajes[n_previos:]:
            if msg.type == "ai":
                canal.send_text(tenant_id, telefono, msg.content)

    pendiente = runtime.interrupt_pendiente_p2(graph_p2, tenant_id, booking_id)
    if pendiente:
        _a_bandeja(tenant_id, pendiente, referencia=booking_id)
    return resultado


def rutear_evento(app_state, tenant_id: str, clave: str, evento_raw: str,
                  telefono: str | None = None) -> dict:
    """Router de eventos del Scheduler: decodifica sufijos discriminadores
    ('timeout_proveedor:{id}') y elige el grafo P1 o P2 según el evento."""
    from graphs.graph_operativo import ENTRYPOINTS_RESUME_P2

    base, _, arg = evento_raw.partition(":")
    if base in ENTRYPOINTS_RESUME_P2:
        payload = {"proveedor_id": arg} if arg else None
        return despachar_evento_p2(app_state.graph_p2, tenant_id, clave,
                                   base, payload=payload)
    return despachar_evento(app_state.graph, tenant_id, clave, evento_raw,
                            telefono=telefono)


# ── Webhook WhatsApp (compartido FastAPI / AgentCore) ────────────────────────
def procesar_webhook_whatsapp(graph, body: dict) -> dict:
    """Regla 4 (at-least-once): cada mensaje se reclama en el dedup ANTES de
    procesarse, y si el procesamiento falla se LIBERA para que el reintento de
    Meta lo reprocese; los ya procesados quedan dedupeados."""
    from core.dedup import get_dedup
    from tools.crm import ensure_lead, lead_para_telefono, tenant_por_whatsapp

    dedup = get_dedup()
    procesados = duplicados = fallidos = 0

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
            tenant_id = tenant_por_whatsapp(phone_number_id)
            if tenant_id is None:
                log.warning("phone_number_id sin tenant: %s", phone_number_id)
                continue

            for msg in value.get("messages", []):
                if not dedup.registrar_si_nuevo("whatsapp", msg["id"], tenant_id,
                                                {"from": msg.get("from")}):
                    duplicados += 1
                    continue
                if msg.get("type") != "text":
                    continue  # audio/imagen: F4+ (por ahora solo texto)
                try:
                    telefono = msg["from"]
                    texto = msg["text"]["body"]
                    lead_id = lead_para_telefono(tenant_id, telefono)
                    ensure_lead(tenant_id, lead_id, telefono=telefono)
                    estado = graph.get_state(
                        runtime.thread_config(tenant_id, lead_id)).values
                    evento = ("cliente_acepta" if es_aceptacion(texto, estado)
                              else "mensaje_cliente")
                    despachar_evento(graph, tenant_id, lead_id, evento,
                                     texto=texto, telefono=telefono)
                    procesados += 1
                except Exception:
                    dedup.eliminar("whatsapp", msg["id"])
                    fallidos += 1
                    log.exception("fallo procesando %s (dedup liberado)", msg["id"])

    return {"status": "ok" if not fallidos else "parcial",
            "procesados": procesados, "duplicados": duplicados,
            "fallidos": fallidos}


# ── Webhook de pagos (compartido FastAPI / AgentCore) ────────────────────────
_SECRETOS_PASARELA = {
    "culqi": lambda: settings.culqi_webhook_secret,
    "mercadopago": lambda: settings.mercadopago_webhook_secret,
}


def verificar_firma_pasarela(pasarela: str, cuerpo: bytes, firma: str) -> bool:
    secreto = _SECRETOS_PASARELA.get(pasarela, lambda: "")()
    if not secreto:
        return settings.aiv_env == "dev"  # dev sin secreto: se permite (stub)
    esperada = hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, firma or "")


def procesar_webhook_pagos(graph, cuerpo: bytes, firma: str) -> tuple[dict, int]:
    """Devuelve (respuesta, status_code). Si el despacho falla tras reclamar el
    dedup, se libera y se re-lanza: la pasarela reintenta y el pago no se pierde."""
    from core.dedup import get_dedup

    try:
        datos = json.loads(cuerpo)
    except json.JSONDecodeError:
        return {"detail": "JSON inválido"}, 400

    pasarela = datos.get("pasarela", "")
    if pasarela not in _SECRETOS_PASARELA:
        return {"detail": f"pasarela desconocida: {pasarela!r}"}, 400
    if not verificar_firma_pasarela(pasarela, cuerpo, firma):
        return {"detail": "firma inválida"}, 401

    transaccion_id = datos.get("transaccion_id")
    meta = datos.get("metadata", {})
    tenant_id, lead_id = meta.get("tenant_id"), meta.get("lead_id")
    if not (transaccion_id and tenant_id and lead_id):
        return {"detail": "faltan transaccion_id/metadata"}, 400

    dedup = get_dedup()
    if not dedup.registrar_si_nuevo(pasarela, transaccion_id, tenant_id, datos):
        log.info("reintento de pasarela ignorado: %s/%s", pasarela, transaccion_id)
        return {"status": "duplicado", "transaccion_id": transaccion_id}, 200

    try:
        despachar_evento(graph, tenant_id, lead_id, "pago_ok",
                         payload={"pasarela_ref": f"{pasarela}:{transaccion_id}"})
    except Exception:
        dedup.eliminar(pasarela, transaccion_id)
        raise

    return {"status": "procesado", "transaccion_id": transaccion_id}, 200


def _a_bandeja(tenant_id: str, pendiente: dict, referencia: str) -> None:
    """Refleja el interrupt en la bandeja del dueño (tabla aprobaciones).
    Best-effort: sin DB (simulador fake) solo queda en el log."""
    log.info("interrupt pendiente para el dueño: %s", pendiente)
    try:
        from tools.aprobaciones import registrar_pendiente
        registrar_pendiente(tenant_id, pendiente.get("tipo", "aprobacion_descuento"),
                            referencia, pendiente)
    except Exception as exc:
        log.warning("bandeja de aprobaciones no disponible: %s", exc)
