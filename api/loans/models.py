from django.conf import settings
from django.db import models


class LoanProduct(models.Model):
    """대출 상품 소개 (F-LOAN-01/02)."""

    class Category(models.TextChoices):
        PERSONAL = "personal", "개인대출"
        BUSINESS = "business", "기업대출"

    category = models.CharField(max_length=10, choices=Category.choices)
    name = models.CharField(max_length=100)
    summary = models.CharField(max_length=200, blank=True)
    target = models.CharField(max_length=200, blank=True)  # 대상
    max_limit = models.BigIntegerField(default=0)
    rate_min = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    rate_max = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    term_desc = models.CharField(max_length=100, blank=True)
    repay_method = models.CharField(max_length=100, blank=True)
    features = models.JSONField(default=list)  # 특장점
    steps = models.JSONField(default=list)  # 절차 STEP
    info = models.JSONField(default=dict)  # 안내 표 (중도상환수수료·연체금리 등)
    faqs = models.JSONField(default=list)
    notices = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class LimitCheck(models.Model):
    """간편 한도 조회 모의 결과 (F-LOAN-03)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    type = models.CharField(max_length=20)
    payload = models.JSONField(default=dict)
    limit = models.BigIntegerField()
    rate_min = models.DecimalField(max_digits=5, decimal_places=2)
    rate_max = models.DecimalField(max_digits=5, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)


class LoanApplication(models.Model):
    """대출 신청 (F-LOAN-04). 승인 시 투자 상품으로 전환."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted"
        APPROVED = "approved"
        REJECTED = "rejected"
        CONVERTED = "converted"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    name = models.CharField(max_length=50)
    phone = models.CharField(max_length=20)
    email = models.EmailField()
    company = models.CharField(max_length=100, blank=True)
    biz_type = models.CharField(max_length=30, blank=True)
    biz_no = models.CharField(max_length=20, blank=True)
    amount = models.BigIntegerField()
    term_months = models.PositiveIntegerField(default=12)
    purpose = models.CharField(max_length=200, blank=True)
    memo = models.TextField(blank=True)
    attachments = models.JSONField(default=list)  # 최대 6개 파일 URL
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.SUBMITTED
    )
    product = models.ForeignKey(
        "products.Product", null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
