import json
import threading
from datetime import date

import psycopg
from django.db import connections
from django.http import StreamingHttpResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import Throttled
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView

from api.accounts.models import REAL_ESTATE_TYPES, GRADE_LIMITS
from api.common.exceptions import NotFound, ValidationFailed
from api.ledger import services as ledger
from api.products.models import Product, ProductProgress
from api.products.schedule import schedule_summary
from api.products.serializers import (
    ProductDetailSerializer,
    ProductListSerializer,
    SchedulePreviewSerializer,
)


class ProductProgressSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    raised_amount = serializers.IntegerField()
    remaining = serializers.IntegerField()
    status = serializers.ChoiceField(choices=Product.Status.choices)


def _parse_ids(raw):
    if raw is None:
        return []
    try:
        ids = [int(value) for value in raw.split(",")]
        if len(ids) > 100 or any(value <= 0 or value > 2**63 - 1 for value in ids):
            raise ValueError
    except ValueError:
        raise ValidationFailed("invalid ids")
    return ids


HEARTBEAT_SEC = 15
NOTIFY_CHANNEL = "product_progress"
STREAM_MAX_PER_IP = 3

_stream_lock = threading.Lock()
_stream_counts = {}


def _acquire_stream(ip):
    with _stream_lock:
        if _stream_counts.get(ip, 0) >= STREAM_MAX_PER_IP:
            return False
        _stream_counts[ip] = _stream_counts.get(ip, 0) + 1
        return True


def _release_stream(ip):
    with _stream_lock:
        left = _stream_counts.get(ip, 0) - 1
        if left > 0:
            _stream_counts[ip] = left
        else:
            _stream_counts.pop(ip, None)


def _listen_connection():
    db = connections["default"].settings_dict
    conn = psycopg.connect(
        dbname=db["NAME"],
        user=db["USER"],
        password=db["PASSWORD"],
        host=db["HOST"],
        port=db["PORT"],
        autocommit=True,
    )
    conn.execute(f"LISTEN {NOTIFY_CHANNEL}")
    return conn


def _await_notify(conn, ids, timeout):
    for notify in conn.notifies(timeout=timeout):
        try:
            payload = json.loads(notify.payload)
        except ValueError:
            continue
        if not ids or payload.get("product_id") in ids:
            return True
    return False


def progress_stream(ids, cursor):
    """SSE stream over PostgreSQL LISTEN/NOTIFY.

    Sync worker model: each open stream pins one WSGI worker plus one
    dedicated psycopg connection blocked in notifies(); disconnect is
    detected on write failure and the connection is released in finally.
    Event ids are commit-ordered: the DB trigger takes a global advisory
    lock before inserting, so id > cursor never misses a late commit.
    """
    conn = _listen_connection()
    try:
        events = ProductProgress.objects.exclude(status=Product.Status.DRAFT).exclude(
            product__status=Product.Status.DRAFT
        )
        if ids:
            events = events.filter(product_id__in=ids)
        pending = []
        if cursor is None:
            pending = sorted(events.order_by("product_id", "-id").distinct("product_id"), key=lambda event: event.id)
            cursor = 0
        fetch_more = True
        while True:
            if fetch_more and not pending:
                pending = list(events.filter(id__gt=cursor).order_by("id")[:100])
            for event in pending:
                data = {"id": event.product_id, "raised_amount": event.raised_amount, "remaining": event.remaining, "status": event.status}
                cursor = event.id
                yield f"id: {cursor}\nevent: progress\ndata: {json.dumps(data)}\n\n"
            fetch_more = len(pending) == 100
            pending = []
            if fetch_more:
                continue
            if _await_notify(conn, ids, HEARTBEAT_SEC):
                fetch_more = True
            else:
                yield ": heartbeat\n\n"
    finally:
        conn.close()


class ProductStreamView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        parameters=[
            OpenApiParameter("ids", str, description="Comma-separated positive product IDs (up to 100). Omit for all public products."),
            OpenApiParameter("Last-Event-ID", int, location=OpenApiParameter.HEADER),
        ],
        responses={(200, "text/event-stream"): ProductProgressSerializer},
    )
    def get(self, request):
        ids = _parse_ids(request.query_params.get("ids"))
        cursor = request.headers.get("Last-Event-ID")
        try:
            cursor = int(cursor) if cursor is not None else None
            if cursor is not None and not 0 <= cursor <= 2**63 - 1:
                raise ValueError
        except ValueError:
            raise ValidationFailed("Invalid Last-Event-ID")
        # X-Forwarded-For는 클라이언트가 위조 가능 — cap 회피 방지를 위해
        # REMOTE_ADDR만 사용 (신뢰 프록시 도입 시 TRUSTED_PROXY 설정으로 확장)
        ip = request.META.get("REMOTE_ADDR", "")
        if not _acquire_stream(ip):
            raise Throttled()
        try:
            response = StreamingHttpResponse(
                progress_stream(ids, cursor),
                content_type="text/event-stream",
                headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
            )
        except Exception:
            _release_stream(ip)
            raise
        response._resource_closers.append(lambda: _release_stream(ip))
        return response


class ProductListView(ListAPIView):
    """GET /api/products — 투자 상품 목록 (F-INV-01)."""

    permission_classes = (AllowAny,)
    serializer_class = ProductListSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter("status", str),
            OpenApiParameter("ids", str, description="Comma-separated positive product IDs (up to 100)."),
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
        qs = Product.objects.exclude(status=Product.Status.DRAFT)
        p = self.request.query_params
        ids = _parse_ids(p.get("ids"))
        if ids:
            qs = qs.filter(id__in=ids)
        status = p.get("status")
        if status:
            qs = qs.filter(status=status)
        elif p.get("include_closed") not in ("1", "true"):
            qs = qs.exclude(
                status__in=[Product.Status.REPAID, Product.Status.LOSS]
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
    from api.investments.services import invested_sums

    limits = GRADE_LIMITS[user.grade]
    invested_total, invested_re, by_borrower = invested_sums(user)
    same_borrower = by_borrower.get(product.borrower_id, 0)

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
        product = Product.objects.filter(pk=pk).exclude(
            status=Product.Status.DRAFT
        ).first()
        if product is None:
            raise NotFound()
        data = ProductDetailSerializer(
            product, context={"request": request}
        ).data
        return Response(data)


class SchedulePreviewView(APIView):
    """GET /api/products/{id}/schedule-preview?amount= — 예상수익 (F-INV-03)."""

    permission_classes = (AllowAny,)

    @extend_schema(responses=SchedulePreviewSerializer)
    def get(self, request, pk):
        product = Product.objects.filter(pk=pk).exclude(
            status=Product.Status.DRAFT
        ).first()
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
