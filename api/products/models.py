from django.db import models

from api.common.exceptions import StateConflict


class Product(models.Model):
    """투자 상품. 상태머신: draft → scheduled → recruiting → recruited
    → executed → repaying → repaid | overdue | loss"""

    class Type(models.TextChoices):
        SCF = "scf", "매출채권"
        STOCK_LOAN = "stock_loan", "주식담보"
        MORTGAGE = "mortgage", "부동산담보"
        PERSONAL_CREDIT = "personal_credit", "개인신용"

    class RepayType(models.TextChoices):
        EQUAL_INSTALLMENT = "equal_installment", "원리금균등"
        EQUAL_PRINCIPAL = "equal_principal", "원금균등"
        BULLET = "bullet", "만기일시"

    class Status(models.TextChoices):
        DRAFT = "draft"
        SCHEDULED = "scheduled"
        RECRUITING = "recruiting"
        RECRUITED = "recruited"
        EXECUTED = "executed"
        REPAYING = "repaying"
        REPAID = "repaid"
        OVERDUE = "overdue"
        LOSS = "loss"

    ALLOWED_TRANSITIONS = {
        Status.DRAFT: {Status.SCHEDULED, Status.RECRUITING},
        Status.SCHEDULED: {Status.RECRUITING},
        Status.RECRUITING: {Status.RECRUITED},
        Status.RECRUITED: {Status.EXECUTED},
        Status.EXECUTED: {Status.REPAYING},
        Status.REPAYING: {Status.REPAID, Status.OVERDUE, Status.LOSS},
        Status.OVERDUE: {Status.REPAID, Status.LOSS},
        Status.REPAID: set(),
        Status.LOSS: set(),
    }

    product_no = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    type = models.CharField(max_length=20, choices=Type.choices)
    annual_rate = models.DecimalField(max_digits=5, decimal_places=2)
    term_months = models.PositiveIntegerField()
    target_amount = models.BigIntegerField()
    raised_amount = models.BigIntegerField(default=0)
    repay_type = models.CharField(max_length=20, choices=RepayType.choices)
    platform_fee_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=0
    )  # 연 %, 잔액 기준
    repay_day = models.PositiveSmallIntegerField(default=25)  # 매월 이자 지급일
    borrower_id = models.CharField(max_length=40, db_index=True)
    borrower_name = models.CharField(max_length=100, blank=True)
    tags = models.JSONField(default=list)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT
    )
    overview = models.JSONField(default=dict)  # 종합정보 탭
    detail = models.JSONField(default=dict)  # 상세정보 탭
    notice = models.TextField(blank=True)  # 유의사항 탭
    refinance_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL
    )
    recruit_open_at = models.DateTimeField(null=True, blank=True)
    registered_at = models.DateTimeField(auto_now_add=True)
    executed_at = models.DateTimeField(null=True, blank=True)

    @property
    def remaining_amount(self):
        return self.target_amount - self.raised_amount

    @property
    def progress_pct(self):
        if not self.target_amount:
            return "0.0"
        return f"{self.raised_amount * 100 / self.target_amount:.1f}"

    def transition(self, to_status):
        allowed = self.ALLOWED_TRANSITIONS.get(self.Status(self.status), set())
        if self.Status(to_status) not in allowed:
            raise StateConflict(
                f"cannot transition {self.status} -> {to_status}",
                {"from": self.status, "to": to_status},
            )
        self.status = to_status
        return self

    class Meta:
        indexes = [models.Index(fields=["status"])]

    def __str__(self):
        return f"{self.product_no} {self.name}"


class ProductDocument(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="documents"
    )
    title = models.CharField(max_length=200)
    file_url = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)


class ProductProgress(models.Model):
    # ponytail: append-only event log, unbounded growth; add created_at +
    # retention purge when row count matters (id cursor ordering suffices now)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    raised_amount = models.BigIntegerField()
    remaining = models.BigIntegerField()
    status = models.CharField(max_length=12)
