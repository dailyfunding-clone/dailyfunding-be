import uuid

from django.conf import settings
from django.db import models


class LedgerEntry(models.Model):
    """이중분개 원장의 분개(leg) 하나.

    amount > 0 이면 해당 계정 잔액 증가(credit 성격), < 0 이면 감소.
    같은 group_id의 합계는 반드시 0 — DB deferred constraint trigger가
    커밋 시점에 검증한다.
    """

    class Kind(models.TextChoices):
        DEPOSIT = "deposit", "입금"
        WITHDRAW_HOLD = "withdraw_hold", "출금 홀드"
        WITHDRAW = "withdraw", "출금 확정"
        WITHDRAW_ROLLBACK = "withdraw_rollback", "출금 롤백"
        INVEST = "invest", "투자 차감"
        REPAY = "repay", "상환 입금"
        LOAN_EXECUTE = "loan_execute", "대출 실행"
        POINT_EARN = "point_earn", "포인트 적립"
        POINT_SPEND = "point_spend", "포인트 사용"
        POINT_EXPIRE = "point_expire", "포인트 소멸"
        FEE = "fee", "수수료"

    group_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    account = models.CharField(max_length=64, db_index=True)
    amount = models.BigIntegerField()
    kind = models.CharField(max_length=20, choices=Kind.choices)
    ref_type = models.CharField(max_length=40, blank=True)
    ref_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)
        indexes = [models.Index(fields=["account", "created_at"])]


class IdempotencyRecord(models.Model):
    """api-spec §13: (user, key) 유니크, 저장된 응답 재생, 24h 보존."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    response_body = models.JSONField(null=True, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "key")
        indexes = [models.Index(fields=["created_at"])]


class DepositIntent(models.Model):
    """F-DEP-02 입금 알리기 → 모의 은행 웹훅 매칭 대상."""

    class Status(models.TextChoices):
        PENDING = "pending"
        CREDITED = "credited"
        HELD = "held"

    id = models.CharField(primary_key=True, max_length=40)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="deposit_intents",
    )
    amount = models.BigIntegerField()
    sender_name = models.CharField(max_length=50)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    held_reason = models.CharField(max_length=200, blank=True)
    event_id = models.CharField(max_length=64, blank=True)
    transfer_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    credited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "status"])]
        constraints = [
            models.UniqueConstraint(
                fields=["transfer_id"],
                condition=~models.Q(transfer_id=""),
                name="deposit_intent_transfer_id_uniq",
            )
        ]

    @staticmethod
    def new_id():
        return f"dep-{uuid.uuid4().hex[:24]}"


class Withdrawal(models.Model):
    """F-DEP-03 출금: requested → processing → completed | failed."""

    class Status(models.TextChoices):
        REQUESTED = "requested"
        PROCESSING = "processing"
        COMPLETED = "completed"
        FAILED = "failed"

    id = models.CharField(primary_key=True, max_length=40)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="withdrawals",
    )
    amount = models.BigIntegerField()
    fee = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.REQUESTED
    )
    failure_reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"])]

    @staticmethod
    def new_id():
        return f"wd-{uuid.uuid4().hex[:24]}"


class PointEntry(models.Model):
    """포인트 원장 (F-PNT-01). earn 행은 remaining으로 소진 추적."""

    class Kind(models.TextChoices):
        EARN = "earn", "적립"
        SPEND = "spend", "사용"
        EXPIRE = "expire", "소멸"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="point_entries",
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    amount = models.BigIntegerField()  # earn: +, spend/expire: -
    remaining = models.BigIntegerField(default=0)  # earn 행의 미소진 잔량
    expires_at = models.DateTimeField(null=True, blank=True)
    ref_type = models.CharField(max_length=40, blank=True)
    ref_id = models.CharField(max_length=64, blank=True)
    memo = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "kind", "expires_at"])]
