import time

from django.conf import settings
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.accounts.models import VirtualAccount
from api.common.exceptions import NotFound, ValidationFailed
from api.ledger.models import DepositIntent, Withdrawal
from mockbank import services
from mockbank.models import WebhookDelivery


def _require_debug():
    if not settings.DEBUG:
        raise NotFound()


class DepositExecuteView(APIView):
    """POST /mockbank/deposits/execute — 입금 완료 웹훅 발행.

    입력: intent_id 또는 (account_no, amount, sender_name).
    실패 주입: force_mismatch(예금주명 불일치), force_bad_signature(위조 서명),
    delay_ms(지연).
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()

    @extend_schema(exclude=True)
    def post(self, request):
        _require_debug()
        d = request.data
        delay_ms = int(d.get("delay_ms") or 0)
        if delay_ms:
            time.sleep(min(delay_ms, 5000) / 1000)

        intent = None
        if d.get("intent_id"):
            intent = DepositIntent.objects.filter(id=d["intent_id"]).first()
            if intent is None:
                raise NotFound("intent not found")

        if intent is not None:
            va = intent.user.virtual_account
            account_no = va.account_no
            amount = intent.amount
            sender = va.holder
        else:
            account_no = d.get("account_no", "")
            amount = int(d.get("amount") or 0)
            sender = d.get("sender_name", "")
            if not (account_no and amount and sender):
                raise ValidationFailed(
                    "intent_id or (account_no, amount, sender_name) required"
                )
            if not VirtualAccount.objects.filter(account_no=account_no).exists():
                raise NotFound("account not found")

        if d.get("force_mismatch"):
            sender = f"{sender}_mismatch"

        payload = {
            "type": "deposit.completed",
            "account_no": account_no,
            "sender_name": sender,
            "amount": amount,
            "occurred_at": timezone.now().isoformat(),
        }
        if d.get("force_bad_signature"):
            payload["_bad_signature"] = True

        delivery = services.publish("/api/webhooks/bank/deposit", payload)
        return Response(
            {
                "event_id": delivery.event_id,
                "delivered": delivery.status == WebhookDelivery.Status.SENT,
                "status_code": delivery.last_status_code,
            }
        )


class TransferExecuteView(APIView):
    """POST /mockbank/transfers/execute — 출금 이체 실행 → 결과 웹훅.

    force_fail: transfer.failed 발행 → API가 홀드 해제 역분개.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()

    @extend_schema(exclude=True)
    def post(self, request):
        _require_debug()
        wd = Withdrawal.objects.filter(id=request.data.get("withdrawal_id")).first()
        if wd is None:
            raise NotFound("withdrawal not found")
        if wd.status == Withdrawal.Status.REQUESTED:
            wd.status = Withdrawal.Status.PROCESSING
            wd.save(update_fields=["status"])
        force_fail = bool(request.data.get("force_fail"))
        payload = {
            "type": "transfer.failed" if force_fail else "transfer.completed",
            "withdrawal_id": wd.id,
            "occurred_at": timezone.now().isoformat(),
        }
        if force_fail:
            payload["reason"] = request.data.get("reason", "mock failure")
        delivery = services.publish("/api/webhooks/bank/transfer", payload)
        return Response(
            {
                "event_id": delivery.event_id,
                "delivered": delivery.status == WebhookDelivery.Status.SENT,
                "status_code": delivery.last_status_code,
            }
        )


class DeliveryListView(APIView):
    """GET /mockbank/deliveries — 발행 이력 조회 (운영 확인용)."""

    permission_classes = (AllowAny,)
    authentication_classes = ()

    @extend_schema(exclude=True)
    def get(self, request):
        _require_debug()
        rows = WebhookDelivery.objects.order_by("-id")[:100]
        return Response(
            {
                "results": [
                    {
                        "id": r.id,
                        "event_id": r.event_id,
                        "url": r.url,
                        "status": r.status,
                        "attempts": r.attempts,
                        "last_status_code": r.last_status_code,
                        "last_error": r.last_error,
                    }
                    for r in rows
                ]
            }
        )
