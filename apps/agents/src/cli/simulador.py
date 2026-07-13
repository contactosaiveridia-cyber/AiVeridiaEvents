"""Simulador CLI de conversación WhatsApp para probar el funnel P1 en local.

Uso:
    python -m cli.simulador                # DB de compose + Ollama
    AIV_FAKE_DB=1 python -m cli.simulador  # sin Postgres (tools en memoria)
    python -m cli.simulador --script demo.txt   # conversación no interactiva

Comandos dentro del chat (simulan los eventos externos del BPMN):
    /acepta           cliente acepta la cotización        (cliente_acepta)
    /pago             pasarela confirma el pago           (pago_ok)
    /timeout          48 h sin respuesta a la cotización  (timeout_cotizacion)
    /timeout_final    7 días sin respuesta                (timeout_final)
    /timeout_pago     24 h sin pagar                      (timeout_pago)
    /timeout_pago2    48 h más sin pagar -> liberar       (timeout_pago_final)
    /aprobar si|no    el dueño responde el interrupt de descuento
    /descuento N      el cliente pidió N% (se aplica en la próxima cotización)
    /estado           muestra el estado del thread
    /salir
"""

import argparse
import os
import sys
import uuid

TENANT_DEMO = "11111111-1111-1111-1111-111111111111"  # Los Jazmines (seed)

EVENTOS = {
    "/acepta": "cliente_acepta",
    "/pago": "pago_ok",
    "/timeout": "timeout_cotizacion",
    "/timeout_final": "timeout_final",
    "/timeout_pago": "timeout_pago",
    "/timeout_pago2": "timeout_pago_final",
}


def _imprimir_respuesta(resultado: dict) -> None:
    mensajes = resultado.get("messages") or []
    if mensajes and mensajes[-1].type == "ai":
        print(f"\n🤖 {mensajes[-1].content}\n")
    if resultado.get("resultado"):
        print(f"── fin del proceso: {resultado['resultado'].upper()} ──")


def main() -> None:
    # Windows: la consola cp1252 no soporta emojis
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Simulador WhatsApp P1")
    parser.add_argument("--tenant", default=TENANT_DEMO)
    parser.add_argument("--lead", default=None, help="uuid del lead (default: nuevo)")
    parser.add_argument("--script", default=None,
                        help="archivo con una línea por turno (no interactivo)")
    args = parser.parse_args()

    if os.getenv("AIV_FAKE_DB") == "1":
        os.environ.setdefault("AIV_CHECKPOINTER", "memory")
        from cli import fakes
        fakes.instalar()
        print("[fake-db] tools en memoria; checkpointer en memoria")

    from graphs import runtime
    from graphs.checkpointer import open_checkpointer
    from graphs.graph_comercial import build_graph
    from tools.crm import ensure_lead

    tenant_id = args.tenant
    lead_id = args.lead or str(uuid.uuid4())
    descuento_pendiente = 0.0

    if args.script:
        with open(args.script, encoding="utf-8") as fh:
            turnos = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
        entrada = iter(turnos)
        leer = lambda: next(entrada, "/salir")
    else:
        leer = lambda: input("👤 ")

    with open_checkpointer() as saver:
        graph = build_graph(saver)
        ensure_lead(tenant_id, lead_id, canal="whatsapp")
        print(f"Simulador P1 — tenant {tenant_id[:8]}… lead {lead_id[:8]}… "
              f"(escribe /salir para terminar)\n")

        while True:
            try:
                linea = leer()
            except (EOFError, KeyboardInterrupt):
                break
            if args.script and linea != "/salir":
                print(f"👤 {linea}")

            if linea == "/salir":
                break
            if linea == "/estado":
                snap = graph.get_state(runtime.thread_config(tenant_id, lead_id))
                v = snap.values
                print(f"   calificacion={v.get('calificacion')}\n"
                      f"   cotizacion={v.get('cotizacion')}\n"
                      f"   estado_pago={v.get('estado_pago')} "
                      f"resultado={v.get('resultado')} next={snap.next}")
                continue
            if linea.startswith("/descuento"):
                descuento_pendiente = float(linea.split()[1])
                print(f"   (el cliente negocia {descuento_pendiente}% — "
                      "se evaluará contra el umbral al cotizar)")
                continue
            if linea.startswith("/aprobar"):
                aprobado = linea.split()[1].lower() in ("si", "sí", "yes")
                pendiente = runtime.interrupt_pendiente(graph, tenant_id, lead_id)
                if pendiente is None:
                    print("   (no hay aprobación pendiente)")
                    continue
                print(f"   [dueño] {'APRUEBA' if aprobado else 'RECHAZA'} "
                      f"{pendiente['descuento_pct']}%")
                resultado = runtime.responder_interrupt(
                    graph, tenant_id, lead_id, aprobado=aprobado)
                _imprimir_respuesta(resultado)
                continue

            if linea in EVENTOS:
                resultado = runtime.procesar_evento(
                    graph, tenant_id, lead_id, EVENTOS[linea])
            else:
                payload = {}
                if descuento_pendiente:
                    payload["descuento_solicitado"] = descuento_pendiente
                    descuento_pendiente = 0.0
                resultado = runtime.procesar_evento(
                    graph, tenant_id, lead_id, "mensaje_cliente",
                    texto=linea, payload=payload)

            pendiente = runtime.interrupt_pendiente(graph, tenant_id, lead_id)
            if pendiente:
                print(f"\n⏸  INTERRUPT al dueño: aprobar descuento de "
                      f"{pendiente['descuento_pct']}% "
                      f"(S/ {pendiente['precio_final']:.2f}). "
                      "Responde con /aprobar si|no\n")
                continue
            _imprimir_respuesta(resultado)


if __name__ == "__main__":
    sys.exit(main())
