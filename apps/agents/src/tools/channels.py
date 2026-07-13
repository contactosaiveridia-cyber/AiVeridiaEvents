"""Channel adapters, decoupled from the graphs (WhatsApp Cloud API first;
Instagram DM later implements the same send contract).

dev (sin WHATSAPP_ACCESS_TOKEN): DevChannel loguea el mensaje saliente (el
simulador y los tests lo capturan). Con token configurado se usa la Cloud API
real, con el phone_number_id del tenant (dato por tenant, no código).
"""

import json
import logging
from typing import Protocol

import httpx

from core.config import settings
from core.db import tenant_connection

log = logging.getLogger("aiveridia.channels")

GRAPH_API = "https://graph.facebook.com/v20.0"


class Channel(Protocol):
    def send_text(self, tenant_id: str, to: str, text: str) -> None: ...
    def send_media(self, tenant_id: str, to: str, url: str, caption: str = "") -> None: ...


class DevChannel:
    def send_text(self, tenant_id: str, to: str, text: str) -> None:
        log.info(json.dumps({"channel": "dev", "tenant_id": tenant_id,
                             "to": to, "text": text}, ensure_ascii=False))

    def send_media(self, tenant_id: str, to: str, url: str, caption: str = "") -> None:
        log.info(json.dumps({"channel": "dev", "tenant_id": tenant_id,
                             "to": to, "media": url, "caption": caption},
                            ensure_ascii=False))


class WhatsAppChannel:
    """WhatsApp Cloud API (Meta). El phone_number_id es del tenant."""

    def __init__(self, access_token: str) -> None:
        self._headers = {"Authorization": f"Bearer {access_token}"}

    def _phone_id(self, tenant_id: str) -> str:
        with tenant_connection(tenant_id) as conn:
            row = conn.execute(
                "select whatsapp_phone_id from tenants where id = %s", (tenant_id,)
            ).fetchone()
        if not row or not row["whatsapp_phone_id"]:
            raise RuntimeError(f"tenant {tenant_id} sin whatsapp_phone_id")
        return row["whatsapp_phone_id"]

    def send_text(self, tenant_id: str, to: str, text: str) -> None:
        r = httpx.post(
            f"{GRAPH_API}/{self._phone_id(tenant_id)}/messages",
            headers=self._headers, timeout=15,
            json={"messaging_product": "whatsapp", "to": to,
                  "type": "text", "text": {"body": text}},
        )
        r.raise_for_status()

    def send_media(self, tenant_id: str, to: str, url: str, caption: str = "") -> None:
        r = httpx.post(
            f"{GRAPH_API}/{self._phone_id(tenant_id)}/messages",
            headers=self._headers, timeout=15,
            json={"messaging_product": "whatsapp", "to": to, "type": "document",
                  "document": {"link": url, "caption": caption}},
        )
        r.raise_for_status()


def get_channel(nombre: str = "whatsapp") -> Channel:
    if settings.whatsapp_access_token:
        return WhatsAppChannel(settings.whatsapp_access_token)
    return DevChannel()
