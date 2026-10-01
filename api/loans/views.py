import hashlib

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.common.exceptions import NotFound, ValidationFailed
from api.loans.models import LimitCheck, LoanApplication, LoanProduct


class LimitCheckSerializer(serializers.Serializer):
    type = serializers.CharField()
    complex = serializers.CharField(required=False, allow_blank=True)
    area = serializers.FloatField(required=False)
    dong = serializers.CharField(required=False, allow_blank=True)
    ho = serializers.CharField(required=False, allow_blank=True)
    biz_no = serializers.CharField(required=False, allow_blank=True)


class LoanApplicationSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50)
    phone = serializers.CharField(max_length=20)
    email = serializers.EmailField()
    company = serializers.CharField(required=False, allow_blank=True)
    biz_type = serializers.CharField(required=False, allow_blank=True)
    biz_no = serializers.CharField(required=False, allow_blank=True)
    amount = serializers.IntegerField(min_value=1)
    term_months = serializers.IntegerField(min_value=1, max_value=60)
    purpose = serializers.CharField(required=False, allow_blank=True)
    memo = serializers.CharField(required=False, allow_blank=True)
    agree_privacy = serializers.BooleanField()
    agree_marketing = serializers.BooleanField(required=False, default=False)


def _mock_limit(d):
    """모의 심사: 입력 해시로 결정론적 한도·금리 산출."""
    seed = int(
        hashlib.sha256(str(sorted(d.items())).encode()).hexdigest()[:8], 16
    )
    if d.get("type") == "mortgage":
        base = min(300_000_000, int((d.get("area") or 59) * 2_200_000))
        limit = max(10_000_000, base - seed % 50_000_000)
        rate = ("6.40", "14.00")
    else:
        limit = 10_000_000 + (seed % 290_000_000)
        rate = ("7.00", "16.90")
    return limit, rate


class LoanProductListView(APIView):
    """GET /api/loans?category= (F-LOAN-01)."""

    permission_classes = (AllowAny,)

    def get(self, request):
        qs = LoanProduct.objects.filter(is_active=True)
        category = request.query_params.get("category", "all")
        if category in ("personal", "business"):
            qs = qs.filter(category=category)
        return Response(
            {
                "results": [
                    {
                        "id": p.id,
                        "category": p.category,
                        "name": p.name,
                        "summary": p.summary,
                        "target": p.target,
                        "max_limit": p.max_limit,
                        "rate_range": [str(p.rate_min), str(p.rate_max)],
                        "term_desc": p.term_desc,
                        "repay_method": p.repay_method,
                    }
                    for p in qs
                ]
            }
        )


class LoanProductDetailView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request, pk):
        p = LoanProduct.objects.filter(pk=pk, is_active=True).first()
        if p is None:
            raise NotFound()
        return Response(
            {
                "id": p.id,
                "category": p.category,
                "name": p.name,
                "summary": p.summary,
                "target": p.target,
                "max_limit": p.max_limit,
                "rate_range": [str(p.rate_min), str(p.rate_max)],
                "term_desc": p.term_desc,
                "repay_method": p.repay_method,
                "features": p.features,
                "steps": p.steps,
                "info": p.info,
                "faqs": p.faqs,
                "notices": p.notices,
            }
        )


class LimitCheckView(APIView):
    """POST /api/loans/limit-check — 간편 한도 조회 모의 (F-LOAN-03)."""

    permission_classes = (AllowAny,)

    @extend_schema(request=LimitCheckSerializer)
    def post(self, request):
        s = LimitCheckSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        limit, (rmin, rmax) = _mock_limit(s.validated_data)
        LimitCheck.objects.create(
            user=request.user if request.user.is_authenticated else None,
            type=s.validated_data["type"],
            payload=s.validated_data,
            limit=limit,
            rate_min=rmin,
            rate_max=rmax,
        )
        return Response({"limit": limit, "rate_range": [rmin, rmax]})


class LoanApplicationView(APIView):
    """POST /api/loans/applications — 대출 신청 (F-LOAN-04)."""

    permission_classes = (AllowAny,)

    @extend_schema(request=LoanApplicationSerializer)
    def post(self, request):
        s = LoanApplicationSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if not s.validated_data["agree_privacy"]:
            raise ValidationFailed(
                "privacy agreement required", {"agree_privacy": False}
            )
        files = request.FILES.getlist("attachments")
        if len(files) > 6:
            raise ValidationFailed("max 6 attachments", {"attachments": "max 6"})
        app = LoanApplication.objects.create(
            user=request.user if request.user.is_authenticated else None,
            attachments=[f.name for f in files],
            **{
                k: v
                for k, v in s.validated_data.items()
                if k not in ("agree_privacy", "agree_marketing")
            },
        )
        return Response({"application_id": app.id, "status": app.status}, status=201)
