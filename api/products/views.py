from datetime import date

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView

from api.accounts.models import REAL_ESTATE_TYPES, GRADE_LIMITS
from api.common.exceptions import NotFound, ValidationFailed
from api.ledger import services as ledger
from api.products.models import Product
from api.products.schedule import schedule_summary
from api.products.serializers import ProductDetailSerializer, ProductListSerializer


class ProductListView(ListAPIView):
    """GET /api/products — 투자 상품 목록 (F-INV-01)."""

    permission_classes = (AllowAny,)
    serializer_class = ProductListSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter("status", str),
            OpenApiParameter("type", str),
            OpenApiParameter("min_rate", float),
            OpenApiParameter("max_rate", float),
            OpenApiParameter("min_term", int),
            OpenApiParameter("max_term", int),
            OpenApiParameter("min_amount", int),
            OpenApiParameter("max_amount", int),
            OpenApiParameter("sort", str),
            OpenApiParameter("include_closed", bool),
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        qs = Product.objects.all()
        p = self.request.query_params
        status = p.get("status")
        if status:
            qs = qs.filter(status=status)
        elif p.get("include_closed") not in ("1", "true"):
            qs = qs.exclude(
                status__in=[
                    Product.Status.REPAID,
                    Product.Status.LOSS,
                    Product.Status.DRAFT,
                ]
            )
        if p.get("type"):
            qs = qs.filter(type=p["type"])
        for param, field, op in (
            ("min_rate", "annual_rate", "gte"),
            ("max_rate", "annual_rate", "lte"),
            ("min_term", "term_months", "gte"),
            ("max_term", "term_months", "lte"),
            ("min_amount", "target_amount", "gte"),
            ("max_amount", "target_amount", "lte"),
        ):
            if p.get(param):
                qs = qs.filter(**{f"{field}__{op}": p[param]})
        sort = p.get("sort", "latest")
        if sort == "rate_asc":
            qs = qs.order_by("annual_rate")
        elif sort == "rate_desc":
            qs = qs.order_by("-annual_rate")
        else:
            qs = qs.order_by("-registered_at")
        return qs


def _my_block(user, product):
    """로그인 시 개인화 블록: 예치금·투자 가능액·한도 잔여."""
    from api.investments.models import Investment

    limits = GRADE_LIMITS[user.grade]
    active = Investment.objects.filter(
        user=user, status__in=[Investment.Status.ACTIVE, Investment.Status.OVERDUE]
    )
    invested_total = sum(i.amount for i in active)
    invested_re = sum(
        i.amount for i in active if i.product.type in REAL_ESTATE_TYPES
    )
    same_borrower = sum(
        i.amount
        for i in active
        if i.product.borrower_id == product.borrower_id
    )

    total_remaining = (
        None if limits["total"] is None else max(0, limits["total"] - invested_total)
    )
    re_remaining = (
        None
        if limits["real_estate"] is None
        else max(0, limits["real_estate"] - invested_re)
    )
    borrower_remaining = (
        None
        if limits["same_borrower"] is None
        else max(0, limits["same_borrower"] - same_borrower)
    )

    caps = [product.remaining_amount]
    if product.type in REAL_ESTATE_TYPES and re_remaining is not None:
        caps.append(re_remaining)
    if total_remaining is not None:
        caps.append(total_remaining)
    if borrower_remaining is not None:
        caps.append(borrower_remaining)
    if limits["per_product_pct"] is not None:
        from decimal import Decimal

        caps.append(
            int(
                Decimal(product.target_amount)
                * Decimal(limits["per_product_pct"])
            )
        )
    investable = min(caps) if caps else product.remaining_amount

    return {
        "deposit": ledger.deposit_balance(user.id),
        "investable": investable,
        "grade_remaining_limit": total_remaining,
        "same_borrower_remaining": borrower_remaining,
    }


class ProductDetailView(APIView):
    """GET /api/products/{id} — 상세 (F-INV-02)."""

    permission_classes = (AllowAny,)

    @extend_schema(responses=ProductDetailSerializer)
    def get(self, request, pk):
        product = Product.objects.filter(pk=pk).first()
        if product is None:
            raise NotFound()
        data = ProductDetailSerializer(product).data
        data["tabs"] = {
            "overview": product.overview,
            "detail": product.detail,
            "notice": product.notice,
        }
        if request.user.is_authenticated:
            data["my"] = _my_block(request.user, product)
        return Response(data)


class SchedulePreviewView(APIView):
    """GET /api/products/{id}/schedule-preview?amount= — 예상수익 (F-INV-03)."""

    permission_classes = (AllowAny,)

    def get(self, request, pk):
        product = Product.objects.filter(pk=pk).first()
        if product is None:
            raise NotFound()
        try:
            amount = int(request.query_params.get("amount", ""))
        except ValueError:
            raise ValidationFailed("amount must be integer", {"amount": "required"})
        if amount <= 0:
            raise ValidationFailed("amount must be positive")
        base = product.executed_at.date() if product.executed_at else date.today()
        return Response(schedule_summary(product, amount, base))
