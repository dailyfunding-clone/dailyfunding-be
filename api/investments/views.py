from django.db import IntegrityError
from django.db.models import Prefetch
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from api.common.auth import require_reauth
from api.common.exceptions import NotFound, StateConflict, ValidationFailed
from api.common.idempotency import run_idempotent
from api.investments import services
from api.investments.models import (
    Cart,
    Investment,
    RepaymentSchedule,
    Reservation,
)
from api.investments.serializers import (
    CartAddResponseSerializer,
    CartAddSerializer,
    CartListResponseSerializer,
    InvestOrderSerializer,
    InvestmentDetailResponseSerializer,
    InvestmentListResponseSerializer,
    InvestmentResponseSerializer,
    ReservationCreateSerializer,
    ReservationEligibleResponseSerializer,
    ReservationPatchSerializer,
    ReservationResponseSerializer,
    SuitabilityQuestionsResponseSerializer,
    SuitabilityResultSerializer,
    SuitabilitySubmitSerializer,
)
from api.products.models import Product


class InvestmentListCreateView(APIView):
    """GET /api/investments — 내 투자 내역 / POST — 투자 주문 (F-INV-04)."""

    @extend_schema(
        operation_id="api_investments_list",
        responses=InvestmentListResponseSerializer,
    )
    def get(self, request):
        qs = (
            Investment.objects.filter(user=request.user)
            .select_related("product")
            .order_by("-id")
        )
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        if request.query_params.get("type"):
            qs = qs.filter(product__type=request.query_params["type"])
        try:
            page = int(request.query_params.get("page", 1))
            size = min(int(request.query_params.get("page_size", 20)), 100)
        except ValueError:
            raise ValidationFailed("page and page_size must be integers")
        total = qs.count()
        rows = qs[(page - 1) * size : page * size]
        return Response(
            {
                "results": [
                    {
                        "id": i.id,
                        "product_id": i.product_id,
                        "product_no": i.product.product_no,
                        "product_name": i.product.name,
                        "type": i.product.type,
                        "amount": i.amount,
                        "points_used": i.points_used,
                        "expected_net_return": i.expected_net_return,
                        "status": i.status,
                        "created_at": i.created_at,
                    }
                    for i in rows
                ],
                "total": total,
                "page": page,
            }
        )

    @extend_schema(
        request=InvestOrderSerializer,
        responses={201: InvestmentResponseSerializer},
    )
    def post(self, request):
        require_reauth(request)
        s = InvestOrderSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        if d.get("confirm") and d["confirm"] != "네":
            raise ValidationFailed("confirm must be '네'", {"confirm": d["confirm"]})

        def handler():
            require_reauth(request)
            investment, rows = services.place_investment(
                request.user, d["product_id"], d["amount"], d.get("use_points", 0)
            )
            return services.invest_response(investment, rows), 201

        return run_idempotent(request, d, handler)


class InvestmentDetailView(APIView):
    """GET /api/investments/{id} — 건별 상세 (회차별 상환 현황)."""

    @extend_schema(responses=InvestmentDetailResponseSerializer)
    def get(self, request, pk):
        inv = (
            Investment.objects.filter(pk=pk, user=request.user)
            .select_related("product")
            .first()
        )
        if inv is None:
            raise NotFound()
        data = services.invest_response_from_db(inv)
        data["product"] = {
            "id": inv.product.id,
            "product_no": inv.product.product_no,
            "name": inv.product.name,
            "type": inv.product.type,
            "annual_rate": str(inv.product.annual_rate),
            "status": inv.product.status,
        }
        data["paid_net"] = sum(
            s.interest_net for s in inv.schedules.filter(status="paid")
        )
        return Response(data)


class SuitabilityTestView(APIView):
    """GET 문항 조회 / POST 제출·채점 (F-INV-05)."""

    @extend_schema(responses=SuitabilityQuestionsResponseSerializer)
    def get(self, request):
        latest = (
            request.user.suitability_tests.filter(passed=True)
            .order_by("-expires_at")
            .first()
        )
        valid_until = (
            latest.expires_at
            if latest and latest.expires_at > timezone.now()
            else None
        )
        return Response(
            {
                "questions": [
                    {k: q[k] for k in ("seq", "text", "answer_options")}
                    for q in services.SUITABILITY_QUESTIONS
                ],
                "valid_until": valid_until,
            }
        )

    @extend_schema(
        request=SuitabilitySubmitSerializer,
        responses=SuitabilityResultSerializer,
    )
    def post(self, request):
        s = SuitabilitySubmitSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        record = services.grade_suitability(
            request.user, s.validated_data["answers"]
        )
        return Response(
            {"passed": record.passed, "expires_at": record.expires_at}
        )


class CartView(APIView):
    """GET/POST /api/cart (F-INV-06)."""

    @extend_schema(responses=CartListResponseSerializer)
    def get(self, request):
        items = (
            Cart.objects.filter(user=request.user)
            .select_related("product")
            .order_by("-id")
        )
        closed_statuses = {
            Product.Status.RECRUITED,
            Product.Status.EXECUTED,
            Product.Status.REPAYING,
            Product.Status.REPAID,
            Product.Status.OVERDUE,
            Product.Status.LOSS,
        }
        results = []
        for item in items:
            p = item.product
            results.append(
                {
                    "id": item.id,
                    "product_id": p.id,
                    "product_no": p.product_no,
                    "name": p.name,
                    "annual_rate": str(p.annual_rate),
                    "term_months": p.term_months,
                    "target_amount": p.target_amount,
                    "remaining_amount": p.remaining_amount,
                    "status": p.status,
                    "closed": p.status in closed_statuses,
                }
            )
        return Response({"results": results, "count": len(results)})

    @extend_schema(
        request=CartAddSerializer,
        responses={201: CartAddResponseSerializer},
    )
    def post(self, request):
        s = CartAddSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        product = Product.objects.filter(pk=s.validated_data["product_id"]).first()
        if product is None:
            raise NotFound("product not found")
        item, _ = Cart.objects.get_or_create(user=request.user, product=product)
        return Response({"id": item.id}, status=201)


class CartItemView(APIView):
    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        deleted, _ = Cart.objects.filter(pk=pk, user=request.user).delete()
        if not deleted:
            raise NotFound()
        return Response(status=204)


class ReservationEligibleView(APIView):
    """GET /api/reservations/eligible — 만기 임박 + 재모집 대상 투자."""

    @extend_schema(responses=ReservationEligibleResponseSerializer)
    def get(self, request):
        soon = timezone.now().date() + timezone.timedelta(days=45)
        invs = list(
            Investment.objects.filter(
                user=request.user, status=Investment.Status.ACTIVE
            )
            .select_related("product")
            .prefetch_related(
                Prefetch(
                    "schedules",
                    queryset=RepaymentSchedule.objects.order_by("-seq"),
                    to_attr="latest_schedules",
                )
            )
        )
        refi_product_ids = set(
            Product.objects.filter(
                refinance_of_id__in=[inv.product_id for inv in invs],
                status__in=[Product.Status.SCHEDULED, Product.Status.RECRUITING],
            ).values_list("refinance_of_id", flat=True)
        )
        results = []
        for inv in invs:
            last = inv.latest_schedules[0] if inv.latest_schedules else None
            if last is None or last.status != RepaymentSchedule.Status.SCHEDULED:
                continue
            if last.due_date > soon:
                continue
            results.append(
                {
                    "investment_id": inv.id,
                    "product_id": inv.product_id,
                    "product_name": inv.product.name,
                    "amount": inv.amount,
                    "maturity_date": last.due_date,
                    "refinance_open": inv.product_id in refi_product_ids,
                }
            )
        return Response({"results": results})


class ReservationListCreateView(APIView):
    @extend_schema(responses=ReservationResponseSerializer(many=True))
    def get(self, request):
        qs = (
            Reservation.objects.filter(investment__user=request.user)
            .select_related("investment__product")
            .order_by("-created_at")
        )
        return Response(
            [
                {
                    "id": r.id,
                    "status": r.status,
                    "amount": r.amount,
                    "investment_id": r.investment_id,
                    "product_name": r.investment.product.name,
                    "created_at": r.created_at,
                }
                for r in qs
            ]
        )

    @extend_schema(
        request=ReservationCreateSerializer,
        responses={201: ReservationResponseSerializer},
    )
    def post(self, request):
        s = ReservationCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        inv = Investment.objects.filter(
            pk=s.validated_data["investment_id"],
            user=request.user,
            status=Investment.Status.ACTIVE,
        ).first()
        if inv is None:
            raise NotFound("investment not found")
        amount = s.validated_data["amount"]
        if amount > inv.amount:
            raise ValidationFailed(
                "amount exceeds invested principal", {"amount": inv.amount}
            )
        try:
            res = Reservation.objects.create(investment=inv, amount=amount)
        except IntegrityError:
            raise StateConflict("reservation already exists")
        return Response(
            {"id": res.id, "status": res.status, "amount": res.amount}, status=201
        )


class ReservationDetailView(APIView):
    def _get(self, request, pk):
        res = Reservation.objects.filter(
            pk=pk, investment__user=request.user
        ).first()
        if res is None:
            raise NotFound()
        return res

    @extend_schema(
        request=ReservationPatchSerializer,
        responses=ReservationResponseSerializer,
    )
    def patch(self, request, pk):
        res = self._get(request, pk)
        if res.status != Reservation.Status.RESERVED:
            raise StateConflict("only reserved can be modified")
        s = ReservationPatchSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if s.validated_data["amount"] > res.investment.amount:
            raise ValidationFailed("amount exceeds invested principal")
        res.amount = s.validated_data["amount"]
        res.save(update_fields=["amount"])
        return Response({"id": res.id, "status": res.status, "amount": res.amount})

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        res = self._get(request, pk)
        if res.status != Reservation.Status.RESERVED:
            raise StateConflict("only reserved can be cancelled")
        res.status = Reservation.Status.CANCELLED
        res.save(update_fields=["status"])
        return Response(status=204)
