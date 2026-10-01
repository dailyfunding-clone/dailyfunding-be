import calendar
from datetime import date

from django.db.models import Sum
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from api.accounts.models import GRADE_LIMITS, GradeRequest
from api.common.auth import require_reauth
from api.common.exceptions import ValidationFailed
from api.investments.models import Investment, RepaymentSchedule
from api.investments.services import invested_sums
from api.ledger import services as ledger


def _fmt(n):
    return n or 0


class DashboardView(APIView):
    """GET /api/me/dashboard (F-MY-01)."""

    def get(self, request):
        user = request.user
        va = getattr(user, "virtual_account", None)
        limits = GRADE_LIMITS[user.grade]
        total, real_estate, _ = invested_sums(user)

        active_invs = Investment.objects.filter(
            user=user, status=Investment.Status.ACTIVE
        )
        paid_net = (
            RepaymentSchedule.objects.filter(
                investment__user=user, status=RepaymentSchedule.Status.PAID
            ).aggregate(s=Sum("interest") - Sum("tax") - Sum("fee"))["s"]
            or 0
        )
        expected_net = sum(
            s.interest_net
            for s in RepaymentSchedule.objects.filter(
                investment__user=user, status=RepaymentSchedule.Status.SCHEDULED
            )
        )
        past = Investment.objects.filter(
            user=user,
            status__in=[Investment.Status.REPAID, Investment.Status.LOSS],
        )
        active_paid_net = sum(
            s.interest_net
            for s in RepaymentSchedule.objects.filter(
                investment__in=active_invs, status=RepaymentSchedule.Status.PAID
            )
        )
        return Response(
            {
                "profile": {
                    "name": user.name,
                    "email": user.email,
                    "grade": user.grade,
                    "identity_verified": user.identity_verified,
                },
                "virtual_account": (
                    {
                        "bank": va.bank_name,
                        "account_no": va.account_no,
                        "holder": va.holder,
                    }
                    if va
                    else None
                ),
                "deposit": ledger.deposit_balance(user.id),
                "points": ledger.point_balance(user.id),
                "limits": {
                    "total_remaining": (
                        None
                        if limits["total"] is None
                        else max(0, limits["total"] - total)
                    ),
                    "mortgage_remaining": (
                        None
                        if limits["real_estate"] is None
                        else max(0, limits["real_estate"] - real_estate)
                    ),
                },
                "active": {
                    "invested": sum(i.amount for i in active_invs),
                    "principal_remaining": sum(
                        s.principal_balance
                        for s in RepaymentSchedule.objects.filter(
                            investment__in=active_invs,
                            status=RepaymentSchedule.Status.SCHEDULED,
                            seq=1,
                        )
                    ),
                    "interest_received_net": active_paid_net,
                    "interest_expected_net": expected_net,
                },
                "past": {
                    "count": past.count(),
                    "interest_received_net": paid_net - active_paid_net,
                },
            }
        )


class CalendarView(APIView):
    """GET /api/me/calendar?year=&month= (F-MY-02)."""

    def get(self, request):
        try:
            year = int(request.query_params.get("year", ""))
            month = int(request.query_params.get("month", ""))
        except ValueError:
            raise ValidationFailed("year and month required")
        _, last_day = calendar.monthrange(year, month)
        qs = RepaymentSchedule.objects.filter(
            investment__user=request.user,
            due_date__gte=date(year, month, 1),
            due_date__lte=date(year, month, last_day),
        )
        days = {}
        for s in qs:
            key = s.due_date.isoformat()
            d = days.setdefault(
                key, {"date": key, "principal": 0, "interest_net": 0, "status": "scheduled"}
            )
            d["principal"] += s.principal
            d["interest_net"] += s.interest_net
            if s.status == RepaymentSchedule.Status.PAID:
                d["status"] = "paid" if d["status"] == "scheduled" else d["status"]
            elif s.status == RepaymentSchedule.Status.OVERDUE:
                d["status"] = "overdue"
        done = qs.filter(status=RepaymentSchedule.Status.PAID)
        sched = qs.exclude(status=RepaymentSchedule.Status.PAID)
        return Response(
            {
                "days": sorted(days.values(), key=lambda x: x["date"]),
                "monthly": {
                    "principal_done": sum(s.principal for s in done),
                    "principal_scheduled": sum(s.principal for s in sched),
                    "interest_done_net": sum(s.interest_net for s in done),
                    "interest_scheduled_net": sum(s.interest_net for s in sched),
                },
            }
        )


class GradeView(APIView):
    """GET /api/me/grade (F-MY-05)."""

    def get(self, request):
        user = request.user
        limits = GRADE_LIMITS[user.grade]
        total, real_estate, _ = invested_sums(user)
        return Response(
            {
                "grade": user.grade,
                "limits": {
                    "total": limits["total"],
                    "real_estate": limits["real_estate"],
                    "same_borrower": limits["same_borrower"],
                    "per_product_pct": limits["per_product_pct"],
                },
                "used": {"total": total, "real_estate": real_estate},
            }
        )


class GradeHistoryView(APIView):
    def get(self, request):
        rows = GradeRequest.objects.filter(user=request.user).order_by("-id")
        return Response(
            {
                "results": [
                    {
                        "id": g.id,
                        "to_grade": g.to_grade,
                        "status": g.status,
                        "created_at": g.created_at,
                        "decided_at": g.decided_at,
                    }
                    for g in rows
                ]
            }
        )


class GradeRequestSerializer(serializers.Serializer):
    to_grade = serializers.ChoiceField(choices=["income_eligible", "professional"])


class GradeRequestView(APIView):
    """POST /api/me/grade-request — 등급 변경 신청 (서류 multipart)."""

    @extend_schema(request=GradeRequestSerializer)
    def post(self, request):
        require_reauth(request)
        s = GradeRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        gr = GradeRequest.objects.create(
            user=request.user,
            to_grade=s.validated_data["to_grade"],
            document=request.FILES.get("document"),
        )
        return Response({"id": gr.id, "status": gr.status}, status=201)


class LimitAssessmentView(APIView):
    """POST /api/me/limit-assessment — 원스톱 한도심사 (모의)."""

    def post(self, request):
        if not request.user.identity_verified:
            raise ValidationFailed("identity verification required first")
        doc = request.FILES.get("document")
        gr = GradeRequest.objects.create(
            user=request.user,
            to_grade="income_eligible",
            document=doc,
        )
        return Response(
            {
                "id": gr.id,
                "status": gr.status,
                "eligible_grades": ["income_eligible", "professional"],
                "simulation": True,
            },
            status=201,
        )


class MyInvestmentsView(APIView):
    """GET /api/me/investments — 투자내역 필터 (F-MY-03)."""

    def get(self, request):
        from api.investments.views import InvestmentListCreateView

        return InvestmentListCreateView().get(request)
