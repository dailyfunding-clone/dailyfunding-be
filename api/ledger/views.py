import csv
from datetime import timedelta

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from api.accounts.models import LinkedAccount
from api.common.auth import require_reauth
from api.common.exceptions import NotFound, ValidationFailed
from api.common.idempotency import run_idempotent
from api.ledger import services
from api.ledger.models import DepositIntent, LedgerEntry, PointEntry
from api.ledger.serializers import (
    AutoChargeSerializer,
    DepositAccountSerializer,
    DepositHistorySerializer,
    DepositIntentResponseSerializer,
    EnabledResponseSerializer,
    LinkedAccountResponseSerializer,
    LinkedAccountSerializer,
    NotifyIntentSerializer,
    PointBalanceSerializer,
    PointConvertResponseSerializer,
    PointConvertSerializer,
    PointHistorySerializer,
    WithdrawResponseSerializer,
    WithdrawSerializer,
)


class DepositAccountView(APIView):
    """GET /api/deposit/account — 가상계좌 + 잔액 (F-DEP-01)."""

    @extend_schema(responses=DepositAccountSerializer)
    def get(self, request):
        va = getattr(request.user, "virtual_account", None)
        if va is None:
            raise NotFound("virtual account not found")
        deposit = services.deposit_balance(request.user.id)
        held = services.held_balance(request.user.id)
        return Response(
            {
                "bank": va.bank_name,
                "account_no": va.account_no,
                "holder": va.holder,
                "deposit": deposit,
                "held": held,
                "withdrawable": deposit - held,
            }
        )


class DepositNotifyIntentView(APIView):
    """POST /api/deposit/notify-intent — 입금 알리기 (F-DEP-02)."""

    @extend_schema(
        request=NotifyIntentSerializer,
        responses={202: DepositIntentResponseSerializer},
    )
    def post(self, request):
        s = NotifyIntentSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        def handler():
            intent = DepositIntent.objects.create(
                id=DepositIntent.new_id(),
                user=request.user,
                amount=s.validated_data["amount"],
                sender_name=s.validated_data["sender_name"],
            )
            return {"intent_id": intent.id, "status": intent.status}, 202

        return run_idempotent(request, s.validated_data, handler)


class WithdrawView(APIView):
    """POST /api/deposit/withdraw — 출금 요청 (F-DEP-03)."""

    @extend_schema(
        request=WithdrawSerializer,
        responses={202: WithdrawResponseSerializer},
    )
    def post(self, request):
        require_reauth(request)
        s = WithdrawSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        def handler():
            amount = s.validated_data.get("amount")
            if s.validated_data.get("all"):
                amount = services.withdrawable(request.user.id)
                if amount <= 0:
                    raise ValidationFailed("nothing to withdraw")
            wd = services.create_withdrawal(request.user, amount)
            return {
                "withdrawal_id": wd.id,
                "fee": wd.fee,
                "status": wd.status,
            }, 202

        return run_idempotent(request, s.validated_data, handler)


class DepositHistoryView(APIView):
    """GET /api/deposit/history — 예치금 내역 (F-DEP-04, F-MY-04)."""

    @extend_schema(responses=DepositHistorySerializer)
    def get(self, request):
        account = services.deposit_acc(request.user.id)
        view = request.query_params.get("view")
        qs = LedgerEntry.objects.filter(account=account)
        if view == "withholding":
            # 원천징수영수증: 상환 분개 중 세금 원천 내역
            qs = LedgerEntry.objects.filter(
                account=services.PAYABLE_TAX, kind=LedgerEntry.Kind.REPAY
            )
        elif view == "platform_fee":
            qs = LedgerEntry.objects.filter(
                account=services.REVENUE_FEE
            )
        else:
            kind = request.query_params.get("kind")
            if kind:
                qs = qs.filter(kind=kind)
        for param, lookup in (("from", "gte"), ("to", "lte")):
            val = request.query_params.get(param)
            if val:
                qs = qs.filter(**{f"created_at__date__{lookup}": val})
        cursor = request.query_params.get("cursor")
        if cursor:
            qs = qs.filter(id__lt=int(cursor))
        rows = list(qs.order_by("-id")[:100])
        next_cursor = str(rows[-1].id) if rows else None
        return Response(
            {
                "results": [
                    {
                        "id": r.id,
                        "kind": r.kind,
                        "amount": r.amount,
                        "ref_type": r.ref_type,
                        "ref_id": r.ref_id,
                        "created_at": r.created_at,
                    }
                    for r in rows
                ],
                "next_cursor": next_cursor,
            }
        )


class LinkedAccountView(APIView):
    """PUT /api/deposit/linked-account — 연결계좌 등록 (F-DEP-05)."""

    @extend_schema(
        request=LinkedAccountSerializer,
        responses=LinkedAccountResponseSerializer,
    )
    def put(self, request):
        require_reauth(request)
        s = LinkedAccountSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        # 모의 본인명의 검증: 예금주가 본인인증 이름과 다르면 거부
        holder = s.validated_data["holder"]
        identity = getattr(request.user, "identity", None)
        if identity and identity.name != holder:
            raise ValidationFailed(
                "holder name does not match identity", {"holder": holder}
            )
        LinkedAccount.objects.update_or_create(
            user=request.user, defaults=s.validated_data
        )
        return Response({"linked": True, **s.validated_data})


class AutoChargeView(APIView):
    """PUT /api/deposit/auto-charge — 간편충전 ON/OFF (F-DEP-05)."""

    @extend_schema(
        request=AutoChargeSerializer, responses=EnabledResponseSerializer
    )
    def put(self, request):
        s = AutoChargeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        la = getattr(request.user, "linked_account", None)
        if la is None:
            raise NotFound("linked account not found")
        la.auto_charge = s.validated_data["enabled"]
        la.save(update_fields=["auto_charge"])
        return Response({"enabled": la.auto_charge})


class PointsView(APIView):
    """GET /api/points (F-PNT-01)."""

    @extend_schema(responses=PointBalanceSerializer)
    def get(self, request):
        return Response(
            {
                "balance": services.point_balance(request.user.id),
                "expiring_this_month": services.expiring_this_month(request.user.id),
            }
        )


class PointsHistoryView(APIView):
    """GET /api/points/history (F-PNT-02). CSV export 지원."""

    @extend_schema(responses=PointHistorySerializer)
    def get(self, request):
        qs = PointEntry.objects.filter(user=request.user)
        kind = request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)
        for param, lookup in (("from", "gte"), ("to", "lte")):
            val = request.query_params.get(param)
            if val:
                qs = qs.filter(**{f"created_at__date__{lookup}": val})
        rows = qs.order_by("-id")[:500]

        if "text/csv" in request.headers.get("Accept", ""):
            resp = HttpResponse(content_type="text/csv; charset=utf-8")
            resp["Content-Disposition"] = 'attachment; filename="points.csv"'
            w = csv.writer(resp)
            w.writerow(["id", "kind", "amount", "memo", "created_at"])
            for r in rows:
                w.writerow([r.id, r.kind, r.amount, r.memo, r.created_at])
            return resp

        return Response(
            {
                "results": [
                    {
                        "id": r.id,
                        "kind": r.kind,
                        "amount": r.amount,
                        "memo": r.memo,
                        "created_at": r.created_at,
                    }
                    for r in rows
                ]
            }
        )


class PointsConvertView(APIView):
    """POST /api/points/convert — 포인트→예치금 전환 (F-PNT-01)."""

    @extend_schema(
        request=PointConvertSerializer,
        responses=PointConvertResponseSerializer,
    )
    def post(self, request):
        s = PointConvertSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        def handler():
            amount = s.validated_data["amount"]
            services.spend_points(
                request.user, amount, ref_type="point_convert", ref_id="self"
            )
            return {
                "converted": amount,
                "points": services.point_balance(request.user.id),
                "deposit": services.deposit_balance(request.user.id),
            }, 200

        return run_idempotent(request, s.validated_data, handler)
