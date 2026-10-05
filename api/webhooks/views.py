import hashlib
import hmac
import json
import time

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.common.exceptions import Unauthorized, ValidationFailed
from api.ledger import services as ledger
from api.ledger.models import DepositIntent, Withdrawal
from api.webhooks.models import WebhookEvent


def sign_payload(body: bytes, ts: int, secret: str) -> str:
    msg = f"{ts}.".encode() + body
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_signature(request) -> str:
    """X-Bank-Signature: t={ts},v1={hmac}. 시계 편차 ±5분."""
    header = request.headers.get("X-Bank-Signature", "")
    try:
        parts = dict(kv.split("=", 1) for kv in header.split(","))
        ts = int(parts["t"])
        sig = parts["v1"]
    except (ValueError, KeyError, AttributeError):
        raise Unauthorized("malformed signature header")
    if abs(time.time() - ts) > settings.BANK_WEBHOOK_TOLERANCE_SEC:
        raise Unauthorized("signature timestamp out of tolerance")
    expected = sign_payload(request.body, ts, settings.BANK_WEBHOOK_SECRET)
    if not hmac.compare_digest(expected, sig):
        raise Unauthorized("signature mismatch")
    return header


class _BankWebhookView(APIView):
    permission_classes = (AllowAny,)
    authentication_classes = ()

    event_type = ""

    @extend_schema(
        request=inline_serializer(
            name="BankWebhookPayload",
            fields={
                "event_id": serializers.CharField(),
                "type": serializers.CharField(),
            },
        ),
        responses=inline_serializer(
            name="BankWebhookAck",
            fields={
                "received": serializers.BooleanField(),
                "deduplicated": serializers.BooleanField(required=False),
            },
        ),
    )
    def post(self, request):
        signature = verify_signature(request)
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            raise ValidationFailed("invalid json")

        event_id = payload.get("event_id", "")
        if not event_id:
            raise ValidationFailed("event_id required")

        # 멱등: event_id 유니크, 재수신 시 200만 반환
        event, created = WebhookEvent.objects.get_or_create(
            event_id=event_id,
            defaults={
                "type": payload.get("type", ""),
                "payload": payload,
                "signature": signature,
            },
        )
        if not created:
            return Response({"received": True, "deduplicated": True})

        with transaction.atomic():
            self.handle(payload, event)
            if event.status == WebhookEvent.Status.RECEIVED:
                event.status = WebhookEvent.Status.PROCESSED
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "processed_at"])
        return Response({"received": True})

    def handle(self, payload, event):  # pragma: no cover
        raise NotImplementedError


class BankDepositWebhookView(_BankWebhookView):
    """POST /api/webhooks/bank/deposit — 입금 완료 통지."""

    def handle(self, payload, event):
        from api.accounts.models import VirtualAccount

        account_no = payload.get("account_no", "")
        amount = int(payload.get("amount") or 0)
        sender = payload.get("sender_name", "")

        va = VirtualAccount.objects.filter(account_no=account_no).first()
        if va is None:
            event.status = WebhookEvent.Status.HELD
            return

        # intent 매칭: 계좌 + 금액 + 예금주명
        intent = (
            DepositIntent.objects.select_for_update()
            .filter(
                user=va.user,
                amount=amount,
                status=DepositIntent.Status.PENDING,
            )
            .order_by("id")
            .first()
        )
        if intent is None:
            intent = DepositIntent.objects.create(
                id=DepositIntent.new_id(),
                user=va.user,
                amount=amount,
                sender_name=sender,
                status=DepositIntent.Status.HELD,
            )

        if sender != va.holder:
            intent.status = DepositIntent.Status.HELD
            intent.held_reason = f"예금주명 불일치: {sender} != {va.holder}"
            intent.event_id = event.event_id
            intent.save(update_fields=["status", "held_reason", "event_id"])
            event.status = WebhookEvent.Status.HELD
            return

        intent.sender_name = sender
        intent.event_id = event.event_id
        intent.save(update_fields=["sender_name", "event_id"])
        ledger.credit_deposit(intent)


class BankTransferWebhookView(_BankWebhookView):
    """POST /api/webhooks/bank/transfer — 출금 이체 결과."""

    def handle(self, payload, event):
        wd = Withdrawal.objects.filter(id=payload.get("withdrawal_id")).first()
        if wd is None:
            event.status = WebhookEvent.Status.HELD
            return
        wd = Withdrawal.objects.select_for_update().get(pk=wd.pk)
        if payload.get("type") == "transfer.failed":
            ledger.fail_withdrawal(wd, payload.get("reason", ""))
        else:
            ledger.complete_withdrawal(wd)
