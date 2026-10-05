"""이중분개 원장 서비스.

계정 네임스페이스:
  deposit:{user_id}      사용자 예치금
  hold:{user_id}         출금 홀드
  investment:{product_id} 상품별 투자 에스크로
  borrower:{product_id}  차입자 실행/상환
  clearing:bank          모의 은행 청산
  revenue:fee            플랫폼 수수료 수입
  payable:tax            원천징수 예수금
  point_liability        포인트 부채
  equity:promo           포인트 적립 대변 계정(마이너스 허용)
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from api.common.exceptions import InsufficientDeposit, ValidationFailed
from api.ledger.models import DepositIntent, LedgerEntry, PointEntry, Withdrawal

CLEARING_BANK = "clearing:bank"
REVENUE_FEE = "revenue:fee"
PAYABLE_TAX = "payable:tax"
POINT_LIABILITY = "point_liability"
EQUITY_PROMO = "equity:promo"


def deposit_acc(user_id):
    return f"deposit:{user_id}"


def hold_acc(user_id):
    return f"hold:{user_id}"


def investment_acc(product_id):
    return f"investment:{product_id}"


def borrower_acc(product_id):
    return f"borrower:{product_id}"


def post(kind, postings, ref_type="", ref_id="", group_id=None):
    """분개 기록. postings = [(account, amount), ...], 합계 0 필수."""
    total = sum(amount for _, amount in postings)
    if total != 0:
        raise ValidationFailed(
            f"ledger postings unbalanced: {total}", {"sum": total}
        )
    gid = group_id or uuid.uuid4()
    LedgerEntry.objects.bulk_create(
        [
            LedgerEntry(
                group_id=gid,
                account=account,
                amount=amount,
                kind=kind,
                ref_type=ref_type,
                ref_id=str(ref_id),
            )
            for account, amount in postings
        ]
    )
    return gid


def balance(account):
    return (
        LedgerEntry.objects.filter(account=account).aggregate(s=Sum("amount"))["s"]
        or 0
    )


def deposit_balance(user_id):
    return balance(deposit_acc(user_id))


def held_balance(user_id):
    return balance(hold_acc(user_id))


def withdrawable(user_id):
    return deposit_balance(user_id) - held_balance(user_id)


# ---- 입금 ----


def credit_deposit(intent: DepositIntent):
    """입금 확정 분개: 청산(은행) → 사용자 예치금."""
    post(
        LedgerEntry.Kind.DEPOSIT,
        [(CLEARING_BANK, -intent.amount), (deposit_acc(intent.user_id), intent.amount)],
        ref_type="deposit_intent",
        ref_id=intent.id,
    )
    intent.status = DepositIntent.Status.CREDITED
    intent.credited_at = timezone.now()
    intent.save(update_fields=["status", "credited_at"])


# ---- 출금 ----


def monthly_withdraw_count(user_id, when=None):
    when = when or timezone.now()
    month_start = when.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return Withdrawal.objects.filter(
        user_id=user_id, created_at__gte=month_start
    ).count()


def daily_withdraw_amount(user_id, when=None):
    when = when or timezone.now()
    day_start = when.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        Withdrawal.objects.filter(
            user_id=user_id,
            created_at__gte=day_start,
            status__in=[
                Withdrawal.Status.REQUESTED,
                Withdrawal.Status.PROCESSING,
                Withdrawal.Status.COMPLETED,
            ],
        ).aggregate(s=Sum("amount"))["s"]
        or 0
    )


def create_withdrawal(user, amount):
    """출금 요청: 수수료 계산 → 예치금 홀드 분개 → requested."""
    from django.conf import settings as s

    fee = 0
    if monthly_withdraw_count(user.id) >= s.WITHDRAW_FREE_COUNT:
        fee = s.WITHDRAW_FEE

    if daily_withdraw_amount(user.id) + amount > s.DAILY_WITHDRAW_LIMIT:
        raise ValidationFailed(
            "daily withdrawal limit exceeded",
            {"daily_limit": s.DAILY_WITHDRAW_LIMIT},
        )

    available = withdrawable(user.id)
    if amount + fee > available:
        raise InsufficientDeposit("insufficient deposit", {"available": available})

    wd = Withdrawal.objects.create(
        id=Withdrawal.new_id(), user=user, amount=amount, fee=fee
    )
    post(
        LedgerEntry.Kind.WITHDRAW_HOLD,
        [
            (deposit_acc(user.id), -(amount + fee)),
            (hold_acc(user.id), amount + fee),
        ],
        ref_type="withdrawal",
        ref_id=wd.id,
    )
    return wd


def complete_withdrawal(wd: Withdrawal):
    """transfer.completed: 홀드 → 은행 청산 + 수수료 수입."""
    if wd.status != Withdrawal.Status.PROCESSING:
        return
    postings = [
        (hold_acc(wd.user_id), -(wd.amount + wd.fee)),
        (CLEARING_BANK, wd.amount),
    ]
    if wd.fee:
        postings.append((REVENUE_FEE, wd.fee))
    post(
        LedgerEntry.Kind.WITHDRAW,
        postings,
        ref_type="withdrawal",
        ref_id=wd.id,
    )
    wd.status = Withdrawal.Status.COMPLETED
    wd.completed_at = timezone.now()
    wd.save(update_fields=["status", "completed_at"])


def fail_withdrawal(wd: Withdrawal, reason=""):
    """transfer.failed: 홀드 해제 역분개로 잔액 복원."""
    if wd.status != Withdrawal.Status.PROCESSING:
        return
    post(
        LedgerEntry.Kind.WITHDRAW_ROLLBACK,
        [
            (hold_acc(wd.user_id), -(wd.amount + wd.fee)),
            (deposit_acc(wd.user_id), wd.amount + wd.fee),
        ],
        ref_type="withdrawal",
        ref_id=wd.id,
    )
    wd.status = Withdrawal.Status.FAILED
    wd.failure_reason = reason[:200]
    wd.save(update_fields=["status", "failure_reason"])


# ---- 포인트 ----

POINT_TTL_DAYS = 365


def grant_points(user, amount, ref_type="", ref_id="", memo="", expires_at=None):
    """적립: point_liability += amount, 만료 추적용 PointEntry 생성."""
    expires_at = expires_at or timezone.now() + timedelta(days=POINT_TTL_DAYS)
    PointEntry.objects.create(
        user=user,
        kind=PointEntry.Kind.EARN,
        amount=amount,
        remaining=amount,
        expires_at=expires_at,
        ref_type=ref_type,
        ref_id=str(ref_id),
        memo=memo,
    )
    post(
        LedgerEntry.Kind.POINT_EARN,
        [(EQUITY_PROMO, -amount), (POINT_LIABILITY, amount)],
        ref_type=ref_type,
        ref_id=str(ref_id),
    )


def point_balance(user_id):
    return (
        PointEntry.objects.filter(user_id=user_id).aggregate(s=Sum("amount"))["s"] or 0
    )


def expiring_this_month(user_id, when=None):
    """당월 소멸 예정 = 이번 달에 만료되는 earn 행의 미소진 잔량."""
    when = when or timezone.now()
    month_start = when.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)
    return (
        PointEntry.objects.filter(
            user_id=user_id,
            kind=PointEntry.Kind.EARN,
            expires_at__gte=month_start,
            expires_at__lt=month_end,
        ).aggregate(s=Sum("remaining"))["s"]
        or 0
    )


def _consume_earns(user_id, amount, kind, ref_type, ref_id, memo=""):
    """FIFO(만료 임박 순)로 earn 잔량을 소진하고 spend/expire 행 생성."""
    remaining_needed = amount
    earns = (
        PointEntry.objects.select_for_update()
        .filter(
            user_id=user_id, kind=PointEntry.Kind.EARN, remaining__gt=0
        )
        .order_by("expires_at", "id")
    )
    for earn in earns:
        if remaining_needed <= 0:
            break
        take = min(earn.remaining, remaining_needed)
        earn.remaining -= take
        earn.save(update_fields=["remaining"])
        remaining_needed -= take
    if remaining_needed > 0:
        raise ValidationFailed("insufficient point lots", {"shortage": remaining_needed})
    PointEntry.objects.create(
        user_id=user_id,
        kind=kind,
        amount=-amount,
        ref_type=ref_type,
        ref_id=str(ref_id),
        memo=memo,
    )


def spend_points(user, amount, ref_type="", ref_id="", memo=""):
    """포인트 사용 → 예치금 전환. 분개: point_liability -= P, deposit += P."""
    if amount <= 0:
        raise ValidationFailed("amount must be positive")
    if point_balance(user.id) < amount:
        raise ValidationFailed(
            "insufficient points", {"balance": point_balance(user.id)}
        )
    _consume_earns(user.id, amount, PointEntry.Kind.SPEND, ref_type, ref_id, memo)
    post(
        LedgerEntry.Kind.POINT_SPEND,
        [(POINT_LIABILITY, -amount), (deposit_acc(user.id), amount)],
        ref_type=ref_type,
        ref_id=str(ref_id),
    )


def expire_points(now=None):
    """월 1일 소멸 배치 (멱등): 만료된 earn 잔량을 소멸 분개.

    expire PointEntry가 이미 생성된 earn 행은 remaining=0이므로 재실행 안전.
    """
    now = now or timezone.now()
    expired = (
        PointEntry.objects.select_for_update()
        .filter(kind=PointEntry.Kind.EARN, remaining__gt=0, expires_at__lte=now)
        .order_by("id")
    )
    count = 0
    for earn in expired:
        amount = earn.remaining
        earn.remaining = 0
        earn.save(update_fields=["remaining"])
        PointEntry.objects.create(
            user=earn.user,
            kind=PointEntry.Kind.EXPIRE,
            amount=-amount,
            ref_type="point_lot",
            ref_id=str(earn.id),
            memo="유효기간 만료",
        )
        post(
            LedgerEntry.Kind.POINT_EXPIRE,
            [(POINT_LIABILITY, -amount), (EQUITY_PROMO, amount)],
            ref_type="point_lot",
            ref_id=str(earn.id),
        )
        count += 1
    return count
