from django.conf import settings
from django.db import models


class SuitabilityTest(models.Model):
    """투자적합성 테스트 (F-INV-05). 통과 시 1년 유효."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="suitability_tests",
    )
    passed = models.BooleanField()
    passed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Investment(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        REPAID = "repaid"
        OVERDUE = "overdue"
        LOSS = "loss"
        CANCELLED = "cancelled"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="investments",
    )
    product = models.ForeignKey(
        "products.Product", on_delete=models.PROTECT, related_name="investments"
    )
    amount = models.BigIntegerField()
    points_used = models.BigIntegerField(default=0)
    expected_net_return = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "status"])]


class RepaymentSchedule(models.Model):
    """회차별 상환 스케줄 (F-INV-08)."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled"
        PAID = "paid"
        OVERDUE = "overdue"

    investment = models.ForeignKey(
        Investment, on_delete=models.CASCADE, related_name="schedules"
    )
    seq = models.PositiveSmallIntegerField()
    due_date = models.DateField()
    principal_balance = models.BigIntegerField()  # 회차 시작 잔액
    principal = models.BigIntegerField()  # 해당 회차 상환 원금
    interest = models.BigIntegerField()  # 세전 이자
    fee = models.BigIntegerField(default=0)  # 플랫폼 이용료
    tax = models.BigIntegerField(default=0)  # 원천징수 15.4%
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.SCHEDULED
    )
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("investment", "seq")
        ordering = ("investment", "seq")
        indexes = [models.Index(fields=["due_date", "status"])]

    @property
    def interest_net(self):
        return self.interest - self.tax - self.fee


class Cart(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart_items"
    )
    product = models.ForeignKey("products.Product", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "product")


class Reservation(models.Model):
    """예약 투자 (F-INV-07): 만기 상품 재모집 시 원금 자동 재투자."""

    class Status(models.TextChoices):
        RESERVED = "reserved"
        CONVERTED = "converted"
        CANCELLED = "cancelled"
        REFUNDED = "refunded"

    investment = models.ForeignKey(
        Investment, on_delete=models.CASCADE, related_name="reservations"
    )
    amount = models.BigIntegerField()
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.RESERVED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    converted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["investment"],
                condition=models.Q(status="reserved"),
                name="uniq_reserved_per_investment",
            )
        ]
