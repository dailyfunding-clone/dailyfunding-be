import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from mockbank.models import WebhookDelivery


def new_event_id():
    return f"evt-{uuid.uuid4().hex[:24]}"


def sign_body(body: bytes, ts: int) -> str:
    sig = hmac.new(
        settings.BANK_WEBHOOK_SECRET.encode(),
        f"{ts}.".encode() + body,
        hashlib.sha256,
    ).hexdigest()
    return f"t={ts},v1={sig}"


def deliver(delivery: WebhookDelivery):
    """웹훅 1회 전송 시도. 성공 시 sent, 실패 시 failed + 백오프."""
    body = json.dumps(delivery.payload, ensure_ascii=False).encode()
    ts = int(time.time())
    headers = {"Content-Type": "application/json"}
    if delivery.payload.get("_bad_signature"):
        headers["X-Bank-Signature"] = f"t={ts},v1=deadbeef"
    else:
        headers["X-Bank-Signature"] = sign_body(body, ts)

    delivery.attempts += 1
    try:
        resp = requests.post(delivery.url, data=body, headers=headers, timeout=10)
        delivery.last_status_code = resp.status_code
        ok = 200 <= resp.status_code < 300
        delivery.last_error = "" if ok else resp.text[:300]
    except requests.RequestException as e:
        ok = False
        delivery.last_status_code = None
        delivery.last_error = str(e)[:300]

    if ok:
        delivery.status = WebhookDelivery.Status.SENT
        delivery.sent_at = timezone.now()
        delivery.next_retry_at = None
    elif delivery.attempts >= delivery.max_attempts:
        delivery.status = WebhookDelivery.Status.DEAD
        delivery.next_retry_at = None
    else:
        delivery.status = WebhookDelivery.Status.FAILED
        delivery.next_retry_at = timezone.now() + timedelta(
            seconds=2 ** delivery.attempts
        )
    delivery.save()
    return delivery


def publish(path: str, payload: dict) -> WebhookDelivery:
    """이벤트 발행: delivery 레코드 생성 후 즉시 1회 전송."""
    payload = dict(payload)
    payload.setdefault("event_id", new_event_id())
    url = f"{settings.MOCKBANK_API_BASE}{path}"
    delivery = WebhookDelivery.objects.create(
        url=url, payload=payload, event_id=payload["event_id"]
    )
    return deliver(delivery)
