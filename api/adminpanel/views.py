import random
from datetime import date, datetime

from django.db import IntegrityError, models, transaction
from django.db.utils import DataError
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from api.accounts.models import GradeRequest, User
from api.common.exceptions import NotFound, StateConflict, ValidationFailed
from api.common.permissions import IsStaff
from api.contents.models import Disclosure, Event, Faq, News, Notice
from api.ledger import services as ledger
from api.ledger.models import DepositIntent
from api.loans.models import LoanApplication
from api.products.models import Product
from api.adminpanel import services as admin


class ProductUpsertSerializer(serializers.Serializer):
    product_no = serializers.CharField(required=False)
    name = serializers.CharField(required=False)
    type = serializers.ChoiceField(choices=Product.Type.choices, required=False)
    annual_rate = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False
    )
    term_months = serializers.IntegerField(required=False)
    target_amount = serializers.IntegerField(required=False)
    repay_type = serializers.ChoiceField(
        choices=Product.RepayType.choices, required=False
    )
    platform_fee_rate = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False
    )
    repay_day = serializers.IntegerField(required=False, min_value=1, max_value=28)
    borrower_id = serializers.CharField(required=False)
    borrower_name = serializers.CharField(required=False, allow_blank=True)
    tags = serializers.ListField(child=serializers.CharField(), required=False)
    overview = serializers.DictField(required=False)
    detail = serializers.DictField(required=False)
    notice = serializers.CharField(required=False, allow_blank=True)
    refinance_of = serializers.IntegerField(required=False, allow_null=True)


class AdminProductSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    product_no = serializers.CharField()
    name = serializers.CharField()
    type = serializers.CharField()
    annual_rate = serializers.CharField()
    term_months = serializers.IntegerField()
    target_amount = serializers.IntegerField()
    raised_amount = serializers.IntegerField()
    status = serializers.CharField()


class AdminProductListResponseSerializer(serializers.Serializer):
    results = AdminProductSerializer(many=True)


class AdminProductCreateResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    product_no = serializers.CharField()
    status = serializers.CharField()


class IdStatusSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()


class ProductStatusRequestSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Product.Status.choices)


class RepaySummarySerializer(serializers.Serializer):
    date = serializers.CharField()
    paid = serializers.IntegerField()
    overdue = serializers.IntegerField()


class ExpireSummarySerializer(serializers.Serializer):
    expired_lots = serializers.IntegerField()


class ReconcileSummarySerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    diffs = serializers.ListField(child=serializers.DictField())
    report_id = serializers.IntegerField()


class TimeAdvanceRequestSerializer(serializers.Serializer):
    date = serializers.DateField(required=False)


class TimeAdvanceResponseSerializer(serializers.Serializer):
    date = serializers.CharField()
    repay = RepaySummarySerializer()
    expire = ExpireSummarySerializer()
    reconcile = ReconcileSummarySerializer()


class AdminGradeRequestItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    user_id = serializers.IntegerField()
    email = serializers.EmailField()
    to_grade = serializers.CharField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()


class AdminGradeRequestListSerializer(serializers.Serializer):
    results = AdminGradeRequestItemSerializer(many=True)


class GradeDecisionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["approve", "reject"])
    reason = serializers.CharField(required=False, allow_blank=True)


class DepositHoldItemSerializer(serializers.Serializer):
    id = serializers.CharField()
    user_id = serializers.IntegerField()
    email = serializers.EmailField()
    amount = serializers.IntegerField()
    sender_name = serializers.CharField()
    held_reason = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()


class DepositHoldListSerializer(serializers.Serializer):
    results = DepositHoldItemSerializer(many=True)


class AdminLoanApplicationItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    company = serializers.CharField(allow_blank=True)
    amount = serializers.IntegerField()
    term_months = serializers.IntegerField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()


class AdminLoanApplicationListSerializer(serializers.Serializer):
    results = AdminLoanApplicationItemSerializer(many=True)


class LoanDecisionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["approve", "reject"])
    name = serializers.CharField(required=False)
    type = serializers.ChoiceField(
        choices=Product.Type.choices, required=False
    )
    annual_rate = serializers.CharField(required=False)
    repay_type = serializers.ChoiceField(
        choices=Product.RepayType.choices, required=False
    )
    platform_fee_rate = serializers.CharField(required=False)
    borrower_id = serializers.CharField(required=False)


class LoanDecisionResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()
    product_id = serializers.IntegerField(allow_null=True)


class SeedProductsRequestSerializer(serializers.Serializer):
    count = serializers.IntegerField(required=False, default=10)
    status = serializers.ChoiceField(
        choices=Product.Status.choices, required=False
    )
    rate_min = serializers.FloatField(required=False)
    rate_max = serializers.FloatField(required=False)
    amount_min = serializers.IntegerField(required=False)
    amount_max = serializers.IntegerField(required=False)
    term_min = serializers.IntegerField(required=False)
    term_max = serializers.IntegerField(required=False)
    seed = serializers.IntegerField(required=False, allow_null=True)


class SeedProductsResponseSerializer(serializers.Serializer):
    created = serializers.IntegerField()
    ids = serializers.ListField(child=serializers.IntegerField())


class ProductListCreateView(APIView):
    permission_classes = (IsStaff,)

    @extend_schema(responses=AdminProductListResponseSerializer)
    def get(self, request):
        qs = Product.objects.all().order_by("-id")
        return Response(
            {
                "results": [
                    {
                        "id": p.id,
                        "product_no": p.product_no,
                        "name": p.name,
                        "type": p.type,
                        "annual_rate": str(p.annual_rate),
                        "term_months": p.term_months,
                        "target_amount": p.target_amount,
                        "raised_amount": p.raised_amount,
                        "status": p.status,
                    }
                    for p in qs
                ]
            }
        )

    @extend_schema(
        request=ProductUpsertSerializer,
        responses={201: AdminProductCreateResponseSerializer},
    )
    def post(self, request):
        s = ProductUpsertSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        required = ["name", "type", "annual_rate", "term_months", "target_amount", "repay_type", "borrower_id"]
        missing = [k for k in required if k not in d]
        if missing:
            raise ValidationFailed("missing fields", {"missing": missing})
        from django.db.models import Max

        year = timezone.now().year
        seq = (Product.objects.aggregate(m=Max("id"))["m"] or 0) + 1
        d.setdefault("product_no", f"{year}-{seq}")
        p = Product.objects.create(status=Product.Status.DRAFT, **d)
        return Response({"id": p.id, "product_no": p.product_no, "status": p.status}, status=201)


class ProductDetailAdminView(APIView):
    permission_classes = (IsStaff,)

    def _get(self, pk):
        p = Product.objects.filter(pk=pk).first()
        if p is None:
            raise NotFound()
        return p

    @extend_schema(
        request=ProductUpsertSerializer, responses=IdStatusSerializer
    )
    def patch(self, request, pk):
        p = self._get(pk)
        s = ProductUpsertSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if p.status not in (Product.Status.DRAFT, Product.Status.SCHEDULED):
            raise StateConflict("only draft/scheduled products are editable")
        for k, v in s.validated_data.items():
            setattr(p, k, v)
        p.save()
        return Response({"id": p.id, "status": p.status})


class ProductStatusView(APIView):
    permission_classes = (IsStaff,)

    @extend_schema(
        request=ProductStatusRequestSerializer, responses=IdStatusSerializer
    )
    def patch(self, request, pk):
        p = Product.objects.filter(pk=pk).first()
        if p is None:
            raise NotFound()
        to_status = request.data.get("status")
        if to_status not in Product.Status.values:
            raise ValidationFailed("invalid status", {"status": to_status})
        admin.transition_product(p, to_status)
        return Response({"id": p.id, "status": p.status})


class ProductExecuteView(APIView):
    permission_classes = (IsStaff,)

    @extend_schema(request=None, responses=IdStatusSerializer)
    def post(self, request, pk):
        p = Product.objects.filter(pk=pk).first()
        if p is None:
            raise NotFound()
        p = admin.execute_loan(p)
        return Response({"id": p.id, "status": p.status})


def _parse_date(request):
    raw = request.query_params.get("date") or request.data.get("date")
    if not raw:
        return timezone.now().date()
    return datetime.fromisoformat(raw).date()


class BatchRepayView(APIView):
    """POST /api/admin/batch/repay?date= — 상환 배치 수동 트리거 (F-ADM-03/04)."""

    permission_classes = (IsStaff,)

    @extend_schema(
        request=None,
        parameters=[OpenApiParameter("date", str)],
        responses=RepaySummarySerializer,
    )
    def post(self, request):
        from jobs.tasks import repay_daily

        summary = repay_daily(run_date=_parse_date(request))
        return Response(summary)


class BatchExpirePointsView(APIView):
    permission_classes = (IsStaff,)

    @extend_schema(
        request=None,
        parameters=[OpenApiParameter("date", str)],
        responses=ExpireSummarySerializer,
    )
    def post(self, request):
        from jobs.tasks import expire_points

        return Response(expire_points(run_date=_parse_date(request)))


class BatchReconcileView(APIView):
    permission_classes = (IsStaff,)

    @extend_schema(
        request=None,
        parameters=[OpenApiParameter("date", str)],
        responses=ReconcileSummarySerializer,
    )
    def post(self, request):
        from jobs.tasks import reconcile_ledger

        return Response(reconcile_ledger(run_date=_parse_date(request)))


class TimeAdvanceView(APIView):
    """POST /api/admin/time/advance {date} — 데모 시간 진행 유틸.

    해당 날짜 기준으로 상환 배치·포인트 소멸·대사를 순차 실행한다.
    """

    permission_classes = (IsStaff,)

    @extend_schema(
        request=TimeAdvanceRequestSerializer,
        responses=TimeAdvanceResponseSerializer,
    )
    def post(self, request):
        from jobs.tasks import expire_points, reconcile_ledger, repay_daily

        d = _parse_date(request)
        repay = repay_daily(run_date=d)
        expire = expire_points(run_date=d)
        recon = reconcile_ledger(run_date=d)
        return Response(
            {"date": str(d), "repay": repay, "expire": expire, "reconcile": recon}
        )


class GradeRequestListView(APIView):
    permission_classes = (IsStaff,)

    @extend_schema(responses=AdminGradeRequestListSerializer)
    def get(self, request):
        qs = GradeRequest.objects.select_related("user").order_by("-id")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response(
            {
                "results": [
                    {
                        "id": g.id,
                        "user_id": g.user_id,
                        "email": g.user.email,
                        "to_grade": g.to_grade,
                        "status": g.status,
                        "created_at": g.created_at,
                    }
                    for g in qs
                ]
            }
        )


class GradeRequestDetailView(APIView):
    permission_classes = (IsStaff,)

    @extend_schema(
        request=GradeDecisionSerializer, responses=IdStatusSerializer
    )
    def patch(self, request, pk):
        g = GradeRequest.objects.filter(pk=pk).first()
        if g is None:
            raise NotFound()
        action = request.data.get("action")
        if g.status != GradeRequest.Status.SUBMITTED:
            raise StateConflict("already decided")
        if action == "approve":
            if g.to_grade not in User.Grade.values:
                raise ValidationFailed("invalid grade")
            g.status = GradeRequest.Status.APPROVED
            g.user.grade = g.to_grade
            g.user.save(update_fields=["grade"])
        elif action == "reject":
            g.status = GradeRequest.Status.REJECTED
            g.reason = request.data.get("reason", "")
        else:
            raise ValidationFailed("action must be approve|reject")
        g.decided_at = timezone.now()
        g.save()
        return Response({"id": g.id, "status": g.status})


class DepositHoldListView(APIView):
    permission_classes = (IsStaff,)

    @extend_schema(responses=DepositHoldListSerializer)
    def get(self, request):
        qs = DepositIntent.objects.filter(
            status=DepositIntent.Status.HELD
        ).select_related("user")
        return Response(
            {
                "results": [
                    {
                        "id": i.id,
                        "user_id": i.user_id,
                        "email": i.user.email,
                        "amount": i.amount,
                        "sender_name": i.sender_name,
                        "held_reason": i.held_reason,
                        "created_at": i.created_at,
                    }
                    for i in qs
                ]
            }
        )


class DepositHoldDetailView(APIView):
    permission_classes = (IsStaff,)

    @extend_schema(request=None, responses=IdStatusSerializer)
    def patch(self, request, pk):
        intent = admin.match_held_deposit(pk, request.user)
        return Response({"id": intent.id, "status": intent.status})


class LoanApplicationListView(APIView):
    permission_classes = (IsStaff,)

    @extend_schema(responses=AdminLoanApplicationListSerializer)
    def get(self, request):
        qs = LoanApplication.objects.all().order_by("-id")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response(
            {
                "results": [
                    {
                        "id": a.id,
                        "name": a.name,
                        "company": a.company,
                        "amount": a.amount,
                        "term_months": a.term_months,
                        "status": a.status,
                        "created_at": a.created_at,
                    }
                    for a in qs
                ]
            }
        )


class LoanApplicationDetailView(APIView):
    """심사 → 승인 시 투자 상품 전환 (F-ADM)."""

    permission_classes = (IsStaff,)

    @extend_schema(
        request=LoanDecisionSerializer,
        responses=LoanDecisionResponseSerializer,
    )
    def patch(self, request, pk):
        app = LoanApplication.objects.filter(pk=pk).first()
        if app is None:
            raise NotFound()
        s = LoanDecisionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        action = s.validated_data["action"]
        if app.status != LoanApplication.Status.SUBMITTED:
            raise StateConflict("already decided")
        if action == "reject":
            app.status = LoanApplication.Status.REJECTED
        elif action == "approve":
            from django.db.models import Max

            d = s.validated_data
            year = timezone.now().year
            seq = (Product.objects.aggregate(m=Max("id"))["m"] or 0) + 1
            product = Product.objects.create(
                product_no=f"{year}-{seq}",
                name=d.get("name") or f"{app.company or app.name} 대출 {seq}호",
                type=d.get("type", Product.Type.PERSONAL_CREDIT),
                annual_rate=d.get("annual_rate", "12.00"),
                term_months=app.term_months,
                target_amount=app.amount,
                repay_type=d.get("repay_type", Product.RepayType.EQUAL_INSTALLMENT),
                platform_fee_rate=d.get("platform_fee_rate", "1.00"),
                borrower_id=d.get("borrower_id", f"borrower-{app.id}"),
                borrower_name=app.company or app.name,
                status=Product.Status.DRAFT,
            )
            app.status = LoanApplication.Status.APPROVED
            app.product = product
        else:
            raise ValidationFailed("action must be approve|reject")
        app.decided_at = timezone.now()
        app.save()
        return Response(
            {
                "id": app.id,
                "status": app.status,
                "product_id": app.product_id,
            }
        )


class SeedProductsView(APIView):
    """POST /api/admin/seed/products — 가상 상품 대량 생성기 (F-ADM-04).

    실제 데이터를 긁어오지 않고 파라미터 범위에서 난수로 생성한다.
    """

    permission_classes = (IsStaff,)

    NAME_TYPES = [
        ("아파트 담보대출", Product.Type.MORTGAGE),
        ("오피스텔 담보대출", Product.Type.MORTGAGE),
        ("매출채권 유동화", Product.Type.SCF),
        ("스탁론", Product.Type.STOCK_LOAN),
        ("개인신용 대출", Product.Type.PERSONAL_CREDIT),
        ("상가 담보대출", Product.Type.MORTGAGE),
        ("이커머스 셀러론", Product.Type.SCF),
    ]
    TAGS = ["조기상환가능", "연장가능", "분할상환", "보증보험"]

    @extend_schema(
        request=SeedProductsRequestSerializer,
        responses={201: SeedProductsResponseSerializer},
    )
    def post(self, request):
        count = int(request.data.get("count", 10))
        status = request.data.get("status")  # 고정 상태(없으면 랜덤)
        rate_min = float(request.data.get("rate_min", 6.0))
        rate_max = float(request.data.get("rate_max", 15.0))
        amount_min = int(request.data.get("amount_min", 10_000_000))
        amount_max = int(request.data.get("amount_max", 500_000_000))
        term_min = int(request.data.get("term_min", 3))
        term_max = int(request.data.get("term_max", 24))
        seed = request.data.get("seed")
        rng = random.Random(seed)

        statuses_open = [
            Product.Status.SCHEDULED,
            Product.Status.RECRUITING,
            Product.Status.RECRUITING,
            Product.Status.RECRUITED,
        ]
        from django.db.models import Max

        year = timezone.now().year
        base_seq = (Product.objects.aggregate(m=Max("id"))["m"] or 0)
        created = []
        for i in range(count):
            name_base, ptype = rng.choice(self.NAME_TYPES)
            target = rng.randrange(amount_min // 10_000, amount_max // 10_000) * 10_000
            st = status or rng.choice(statuses_open)
            # 원장 정합성: raised_amount는 실제 투자 주문으로만 올린다.
            # 시드 봇이 실주문을 넣어 모집액을 채운다 (대사 배치가 검증 가능).
            if st == Product.Status.SCHEDULED:
                fill_ratio = 0.0
            elif st == Product.Status.RECRUITED:
                fill_ratio = 1.0
            else:
                fill_ratio = rng.choice([0.2, 0.4, 0.7])
            p = Product.objects.create(
                product_no=f"{year}-{base_seq + i + 1}",
                name=f"{name_base} {base_seq + i + 1}호",
                type=ptype,
                annual_rate=round(rng.uniform(rate_min, rate_max), 2),
                term_months=rng.randint(term_min, term_max),
                target_amount=target,
                repay_type=rng.choice(list(Product.RepayType.values)),
                platform_fee_rate=round(rng.uniform(0, 2), 2),
                repay_day=rng.randint(1, 28),
                borrower_id=f"borrower-{rng.randint(1, max(1, count // 3))}",
                borrower_name=f"차주{rng.randint(1, count)}",
                tags=rng.sample(self.TAGS, rng.randint(0, 2)),
                status=(
                    Product.Status.RECRUITING
                    if st in (Product.Status.RECRUITING, Product.Status.RECRUITED)
                    else st
                ),
                recruit_open_at=timezone.now()
                if st != Product.Status.SCHEDULED
                else None,
            )
            if fill_ratio > 0:
                _seed_investments(p, int(target * fill_ratio), rng)
            created.append(p.id)
        return Response({"created": len(created), "ids": created}, status=201)


def _seed_bots(n=5):
    """시드용 전문투자자 봇 — 충분한 예치금 + 적합성 통과 상태."""
    from api.investments.models import SuitabilityTest

    bots = []
    for i in range(n):
        email = f"seedbot{i + 1}@demo.local"
        u = User.objects.filter(email=email).first()
        if u is None:
            u = User.objects.create_user(
                email=email,
                password="SeedBot1234!",
                grade=User.Grade.PROFESSIONAL,
                name=f"시드봇{i + 1}",
            )
            SuitabilityTest.objects.create(
                user=u,
                passed=True,
                passed_at=timezone.now(),
                expires_at=timezone.now() + timezone.timedelta(days=3650),
            )
        if ledger.deposit_balance(u.id) < 1_000_000_000:
            intent = DepositIntent.objects.create(
                id=DepositIntent.new_id(),
                user=u,
                amount=2_000_000_000,
                sender_name=u.name,
            )
            ledger.credit_deposit(intent)
        bots.append(u)
    return bots


def _seed_investments(product, target_raised, rng):
    """시드 봇으로 실제 투자 주문을 넣어 raised_amount를 채운다."""
    from api.investments.services import place_investment

    bots = _seed_bots()
    remaining = target_raised
    for bot in bots:
        while remaining > 0:
            # 전문 등급 상품별 상한(40%)을 넘지 않는 선에서 최대 투자
            cap = int(product.target_amount * 4 // 10)
            mine = sum(
                i.amount for i in bot.investments.filter(product=product)
            )
            amt = min(remaining, cap - mine)
            if amt <= 0:
                break
            try:
                place_investment(bot, product.id, amt)
                remaining -= amt
            except Exception:
                break
    return target_raised - remaining


# ---- 콘텐츠 CRUD (F-ADM-05) ----


def _free_form(name, fields):
    """모델 필드를 자유형(JSON) 속성으로 노출하는 스키마용 시리얼라이저."""
    return inline_serializer(
        name=name,
        fields={f: serializers.JSONField(required=False) for f in fields},
    )


def _validate_crud_payload(model, data):
    for f, v in data.items():
        field = model._meta.get_field(f)
        if f == "month" and not (isinstance(v, int) and 1 <= v <= 12):
            raise ValidationFailed("month must be an integer between 1 and 12")
        if isinstance(field, models.JSONField):
            if field.default is list and not isinstance(v, list):
                raise ValidationFailed(f"{f} must be an array")
            if field.default is dict and not isinstance(v, dict):
                raise ValidationFailed(f"{f} must be an object")


def _save_crud(fn):
    try:
        with transaction.atomic():
            return fn()
    except (IntegrityError, DataError) as e:
        raise ValidationFailed("invalid or duplicate data") from e


def _crud_list_create(model, fields, name):
    req = _free_form(f"Admin{name}Upsert", fields)
    resp = inline_serializer(
        name=f"Admin{name}ListResponse",
        fields={"results": serializers.ListField(child=serializers.JSONField())},
    )
    created = inline_serializer(
        name=f"Admin{name}Created",
        fields={"id": serializers.IntegerField()},
    )

    class V(APIView):
        permission_classes = (IsStaff,)

        @extend_schema(responses=resp)
        def get(self, request):
            rows = model.objects.all().order_by("-id")
            return Response(
                {"results": [{f: getattr(r, f) for f in fields + ["id"]} for r in rows]}
            )

        @extend_schema(request=req, responses={201: created})
        def post(self, request):
            data = {f: request.data.get(f) for f in fields if f in request.data}
            _validate_crud_payload(model, data)
            obj = _save_crud(lambda: model.objects.create(**data))
            return Response({"id": obj.id}, status=201)

    V.__name__ = f"Admin{name}ListCreateView"
    return V


def _crud_detail(model, fields, name):
    req = _free_form(f"Admin{name}Patch", fields)
    resp = inline_serializer(
        name=f"Admin{name}Detail", fields={"id": serializers.IntegerField()}
    )

    class V(APIView):
        permission_classes = (IsStaff,)

        def _get(self, pk):
            obj = model.objects.filter(pk=pk).first()
            if obj is None:
                raise NotFound()
            return obj

        @extend_schema(request=req, responses=resp)
        def patch(self, request, pk):
            obj = self._get(pk)
            data = {f: request.data[f] for f in fields if f in request.data}
            _validate_crud_payload(model, data)
            for f, v in data.items():
                setattr(obj, f, v)
            _save_crud(obj.save)
            return Response({"id": obj.id})

        @extend_schema(responses={204: None})
        def delete(self, request, pk):
            self._get(pk).delete()
            return Response(status=204)

    V.__name__ = f"Admin{name}DetailView"
    return V


AdminNoticeList = _crud_list_create(
    Notice, ["category", "title", "body", "attachments"], "Notice"
)
AdminNoticeDetail = _crud_detail(
    Notice, ["category", "title", "body", "attachments"], "Notice"
)
AdminFaqList = _crud_list_create(
    Faq, ["category", "question", "answer"], "Faq"
)
AdminFaqDetail = _crud_detail(
    Faq, ["category", "question", "answer"], "Faq"
)
AdminEventList = _crud_list_create(
    Event,
    ["title", "summary", "body", "status", "thumbnail_url", "reward_points",
     "start_at", "end_at"],
    "Event",
)
AdminEventDetail = _crud_detail(
    Event,
    ["title", "summary", "body", "status", "thumbnail_url", "reward_points",
     "start_at", "end_at"],
    "Event",
)
AdminDisclosureList = _crud_list_create(
    Disclosure,
    ["year", "month", "kpi", "management", "operations", "internal"],
    "Disclosure",
)
AdminDisclosureDetail = _crud_detail(
    Disclosure,
    ["year", "month", "kpi", "management", "operations", "internal"],
    "Disclosure",
)
AdminNewsList = _crud_list_create(
    News, ["title", "source", "url", "thumbnail_url", "published_at"], "News"
)
AdminNewsDetail = _crud_detail(
    News, ["title", "source", "url", "thumbnail_url", "published_at"], "News"
)
