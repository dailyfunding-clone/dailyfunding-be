import random
from datetime import date, datetime

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
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


class ProductListCreateView(APIView):
    permission_classes = (IsStaff,)

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

    @extend_schema(request=ProductUpsertSerializer)
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

    @extend_schema(request=ProductUpsertSerializer)
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

    def post(self, request):
        from jobs.tasks import repay_daily

        summary = repay_daily(run_date=_parse_date(request))
        return Response(summary)


class BatchExpirePointsView(APIView):
    permission_classes = (IsStaff,)

    def post(self, request):
        from jobs.tasks import expire_points

        return Response(expire_points(run_date=_parse_date(request)))


class BatchReconcileView(APIView):
    permission_classes = (IsStaff,)

    def post(self, request):
        from jobs.tasks import reconcile_ledger

        return Response(reconcile_ledger(run_date=_parse_date(request)))


class TimeAdvanceView(APIView):
    """POST /api/admin/time/advance {date} — 데모 시간 진행 유틸.

    해당 날짜 기준으로 상환 배치·포인트 소멸·대사를 순차 실행한다.
    """

    permission_classes = (IsStaff,)

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

    def patch(self, request, pk):
        intent = admin.match_held_deposit(pk, request.user)
        return Response({"id": intent.id, "status": intent.status})


class LoanApplicationListView(APIView):
    permission_classes = (IsStaff,)

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

    def patch(self, request, pk):
        app = LoanApplication.objects.filter(pk=pk).first()
        if app is None:
            raise NotFound()
        action = request.data.get("action")
        if app.status != LoanApplication.Status.SUBMITTED:
            raise StateConflict("already decided")
        if action == "reject":
            app.status = LoanApplication.Status.REJECTED
        elif action == "approve":
            from django.db.models import Max

            d = request.data
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

    NAMES = [
        "아파트 담보대출",
        "오피스텔 담보대출",
        "매출채권 유동화",
        "스탁론",
        "개인신용 대출",
        "상가 담보대출",
        "이커머스 셀러론",
    ]
    TAGS = ["조기상환가능", "연장가능", "분할상환", "보증보험"]

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

        types = list(Product.Type.values)
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
            ptype = rng.choice(types)
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
                name=f"{rng.choice(self.NAMES)} {base_seq + i + 1}호",
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


def _crud_list_create(model, fields):
    class V(APIView):
        permission_classes = (IsStaff,)

        def get(self, request):
            rows = model.objects.all().order_by("-id")
            return Response(
                {"results": [{f: getattr(r, f) for f in fields + ["id"]} for r in rows]}
            )

        def post(self, request):
            data = {f: request.data.get(f) for f in fields if f in request.data}
            obj = model.objects.create(**data)
            return Response({"id": obj.id}, status=201)

    return V


def _crud_detail(model, fields):
    class V(APIView):
        permission_classes = (IsStaff,)

        def _get(self, pk):
            obj = model.objects.filter(pk=pk).first()
            if obj is None:
                raise NotFound()
            return obj

        def patch(self, request, pk):
            obj = self._get(pk)
            for f in fields:
                if f in request.data:
                    setattr(obj, f, request.data[f])
            obj.save()
            return Response({"id": obj.id})

        def delete(self, request, pk):
            self._get(pk).delete()
            return Response(status=204)

    return V


AdminNoticeList = _crud_list_create(Notice, ["category", "title", "body", "attachments"])
AdminNoticeDetail = _crud_detail(Notice, ["category", "title", "body", "attachments"])
AdminFaqList = _crud_list_create(Faq, ["category", "question", "answer"])
AdminFaqDetail = _crud_detail(Faq, ["category", "question", "answer"])
AdminEventList = _crud_list_create(
    Event,
    ["title", "summary", "body", "status", "thumbnail_url", "reward_points",
     "start_at", "end_at"],
)
AdminEventDetail = _crud_detail(
    Event,
    ["title", "summary", "body", "status", "thumbnail_url", "reward_points",
     "start_at", "end_at"],
)
AdminDisclosureList = _crud_list_create(
    Disclosure, ["year", "month", "kpi", "management", "operations", "internal"]
)
AdminDisclosureDetail = _crud_detail(
    Disclosure, ["year", "month", "kpi", "management", "operations", "internal"]
)
AdminNewsList = _crud_list_create(
    News, ["title", "source", "url", "thumbnail_url", "published_at"]
)
AdminNewsDetail = _crud_detail(
    News, ["title", "source", "url", "thumbnail_url", "published_at"]
)
