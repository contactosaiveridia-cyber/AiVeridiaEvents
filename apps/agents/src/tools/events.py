"""Domain events. `reserva.confirmada` chains P1 -> P2 (message end event).

dev: in-process registry (the simulator and tests subscribe).
prod: EventBridge PutEvents on the domain bus (wired in F8; same publish()).
"""

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from core.config import settings

log = logging.getLogger("aiveridia.events")

_subscribers: dict[str, list[Callable[..., None]]] = {}


def subscribe(detail_type: str, handler: Callable[..., None]) -> None:
    _subscribers.setdefault(detail_type, []).append(handler)


def publish(detail_type: str, **detail) -> None:
    payload = {"detail_type": detail_type,
               "detail": detail,
               "at": datetime.now(timezone.utc).isoformat()}
    log.info(json.dumps(payload, ensure_ascii=False, default=str))

    if settings.aiv_env == "prod":
        import boto3

        boto3.client("events").put_events(Entries=[{
            "Source": "aiveridia.events",
            "DetailType": detail_type,
            "Detail": json.dumps(detail, default=str),
            "EventBusName": "aiveridia-events-prod",
        }])
        return

    for handler in _subscribers.get(detail_type, []):
        handler(**detail)
