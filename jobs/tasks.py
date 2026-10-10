"""Celery 배치 (project-plan §6). 전부 멱등 — 재실행 시 중복 지급 없음."""
import logging
from datetime import date, datetime

from celery import shared_task
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from api.investments.models import Investment, RepaymentSchedule
from api.ledger import services as ledger
from api.ledger.models import LedgerEntry
from api.notifications.models import Notification, notify
from api.products.models import Product, ProductProgress
from jobs.models import BatchRun, ReconcileReport

logger = logging.getLogger(__name__)


def _as_date(value) -> date:
    if value is None:
        return timezone.now().date()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)).date()


@shared_task(name="jobs.tasks.repay_daily")
def repay_daily(run_date=None):
    """repay.daily: 지급일 도래 스케줄 → 원장 상환 분개 + 연체 전환.

    멱등성: (investment, seq) 유니크 + status 전이로 동일 회차 재지급 불가.
    """
    run_date = _as_date(run_date)
    paid_count = 0
    overdue_count = 0

    with transaction.atomic():
        # 1) 지급일 경과 미지급 → 연체 전환
        overdue_qs = RepaymentSchedule.objects.select_for_update().filter(
            status=RepaymentSchedule.Status.SCHEDULED, due_date__lt=run_date
        )
        for s in overdue_qs:
            s.status = RepaymentSchedule.Status.OVERDUE
            s.save(update_fields=["status"])
            overdue_count += 1
        if overdue_count:
            Investment.objects.filter(
                schedules__status=RepaymentSchedule.Status.OVERDUE,
                status=Investment.Status.ACTIVE,
            ).distinct().update(status=Investment.Status.OVERDUE)
            Product.objects.filter(
                investments__status=Investment.Status.OVERDUE,
                status=Product.Status.REPAYING,
            ).distinct().update(status=Product.Status.OVERDUE)

        # 2) 지급: due_date <= run_date && 미지급 (연체 표시분 추집 포함)
        due_qs = (
            RepaymentSchedule.objects.select_for_update()
            .filter(
                status__in=[
                    RepaymentSchedule.Status.SCHEDULED,
                    RepaymentSchedule.Status.OVERDUE,
                ],
                due_date__lte=run_date,
            )
            .select_related("investment", "investment__user", "investment__product")
        )
        for s in due_qs:
            inv = s.investment
            product = inv.product
            gross = s.principal + s.interest
            net = gross - s.tax - s.fee
            postings = [
                (ledger.borrower_acc(product.id), -gross),
                (ledger.deposit_acc(inv.user_id), net),
            ]
            if s.tax:
                postings.append((ledger.PAYABLE_TAX, s.tax))
            if s.fee:
                postings.append((ledger.REVENUE_FEE, s.fee))
            ledger.post(
                LedgerEntry.Kind.REPAY,
                postings,
                ref_type="repayment_schedule",
                ref_id=s.id,
            )
            s.status = RepaymentSchedule.Status.PAID
            s.paid_at = timezone.now()
            s.save(update_fields=["status", "paid_at"])
            paid_count += 1
            notify(
                [inv.user_id],
                Notification.Kind.REPAYMENT,
                f"{product.name} {s.seq}회차 상환",
                f"세후 {net:,}원이 예치금에 입금되었습니다.",
                ref_id=s.id,
            )

        # 3) 투자·상품 종결: 모든 회차 지급 완료 시
        for inv in Investment.objects.filter(
            schedules__due_date__lte=run_date, status__in=[
                Investment.Status.ACTIVE, Investment.Status.OVERDUE
            ]
        ).distinct():
            if not inv.schedules.exclude(
                status=RepaymentSchedule.Status.PAID
            ).exists():
                inv.status = Investment.Status.REPAID
                inv.save(update_fields=["status"])
        for p in Product.objects.filter(status__in=[
            Product.Status.REPAYING, Product.Status.OVERDUE
        ]):
            if p.investments.exists() and not p.investments.exclude(
                status__in=[Investment.Status.REPAID, Investment.Status.CANCELLED]
            ).exists():
                p.status = Product.Status.REPAID
                p.save(update_fields=["status"])

    summary = {"date": str(run_date), "paid": paid_count, "overdue": overdue_count}
    BatchRun.objects.create(name="repay_daily", run_for=run_date, summary=summary)
    logger.info("repay_daily %s", summary)
    return summary


@shared_task(name="jobs.tasks.expire_points")
def expire_points(run_date=None):
    """points.expire: 만료 포인트 소멸 분개 (월 1일). 멱등."""
    when = timezone.now()
    if run_date:
        d = _as_date(run_date)
        when = timezone.make_aware(datetime(d.year, d.month, d.day))
    with transaction.atomic():
        count = ledger.expire_points(now=when)
    summary = {"expired_lots": count}
    BatchRun.objects.create(
        name="expire_points", run_for=when.date(), summary=summary
    )
    return summary


@shared_task(name="jobs.tasks.reconcile_ledger")
def reconcile_ledger(run_date=None):
    """ledger.reconcile: 차대변·잔액·스케줄 정합성 대사."""
    diffs = []

    # 1) 분개 그룹 차대 합계 0 (DB 트리거가 이미 강제하지만 재검증)
    bad_groups = (
        LedgerEntry.objects.values("group_id")
        .annotate(total=Sum("amount"))
        .exclude(total=0)
    )
    for g in bad_groups:
        diffs.append({"check": "group_balanced", "group_id": str(g["group_id"]), "sum": g["total"]})

    # 2) 사용자 계정 음수 잔액
    for acc in (
        LedgerEntry.objects.values("account")
        .annotate(total=Sum("amount"))
        .filter(account__regex=r"^(deposit|hold|investment):")
    ):
        if acc["total"] < 0:
            diffs.append({"check": "non_negative", **acc})

    # 차입자 계정: 이자는 차주 부담분이라 완납 시 -이자총액까지 음수가 정상.
    # 하한 미만으로 내려간 경우만 이상으로 본다.
    interest_by_product = {
        r["investment__product_id"]: r["s"]
        for r in RepaymentSchedule.objects.values(
            "investment__product_id"
        ).annotate(s=Sum("interest"))
    }
    for acc in (
        LedgerEntry.objects.values("account")
        .annotate(total=Sum("amount"))
        .filter(account__regex=r"^borrower:")
    ):
        product_id = int(acc["account"].split(":", 1)[1])
        floor = -(interest_by_product.get(product_id) or 0)
        if acc["total"] < floor:
            diffs.append({"check": "non_negative", "floor": floor, **acc})

    # 3) 투자별 스케줄 원금 합계 = 투자금
    for inv in Investment.objects.exclude(status=Investment.Status.CANCELLED):
        s = inv.schedules.aggregate(p=Sum("principal"))["p"] or 0
        if s != inv.amount:
            diffs.append(
                {
                    "check": "schedule_principal",
                    "investment_id": inv.id,
                    "expected": inv.amount,
                    "actual": s,
                }
            )

    # 4) 상품 모집액 = 활성 투자 합계
    for p in Product.objects.exclude(status=Product.Status.DRAFT):
        invested = (
            p.investments.exclude(
                status__in=[Investment.Status.CANCELLED]
            ).aggregate(s=Sum("amount"))["s"]
            or 0
        )
        if invested != p.raised_amount:
            diffs.append(
                {
                    "check": "raised_amount",
                    "product_id": p.id,
                    "expected": p.raised_amount,
                    "actual": invested,
                }
            )

    # 5) 포인트 부채 = 미소진 earn 잔량 합
    from api.ledger.models import PointEntry

    liability = ledger.balance(ledger.POINT_LIABILITY)
    remaining = (
        PointEntry.objects.filter(kind=PointEntry.Kind.EARN).aggregate(
            s=Sum("remaining")
        )["s"]
        or 0
    )
    if liability != remaining:
        diffs.append(
            {
                "check": "point_liability",
                "expected": remaining,
                "actual": liability,
            }
        )

    report = ReconcileReport.objects.create(
        run_at=timezone.now(), ok=not diffs, diffs=diffs
    )
    BatchRun.objects.create(
        name="reconcile_ledger",
        run_for=_as_date(run_date),
        summary={"ok": not diffs, "diffs": len(diffs)},
    )
    logger.info("reconcile ok=%s diffs=%d", not diffs, len(diffs))
    return {"ok": not diffs, "diffs": diffs, "report_id": report.id}


@shared_task(name="jobs.tasks.retry_webhooks")
def retry_webhooks():
    """webhook.retry: 실패 전송 건 지수 백오프 재전송 (최대 8회)."""
    from mockbank.models import WebhookDelivery
    from mockbank.services import deliver

    now = timezone.now()
    qs = WebhookDelivery.objects.filter(
        status=WebhookDelivery.Status.FAILED,
        next_retry_at__lte=now,
        attempts__lt=8,
    )
    retried = 0
    for d in qs:
        deliver(d)
        retried += 1
    return {"retried": retried}


@shared_task(name="jobs.tasks.purge_idempotency_records")
def purge_idempotency_records():
    """idempotency.purge: TTL(기본 24h) 경과 멱등 레코드 삭제."""
    from django.conf import settings

    from api.ledger.models import IdempotencyRecord

    cutoff = timezone.now() - timezone.timedelta(
        hours=settings.IDEMPOTENCY_TTL_HOURS
    )
    deleted, _ = IdempotencyRecord.objects.filter(
        created_at__lt=cutoff
    ).delete()
    return {"deleted": deleted}


@shared_task(name="jobs.tasks.purge_product_progress")
def purge_product_progress():
    """productprogress.purge: 24h 경과 진행 이벤트 삭제. 상품별 최신 1건은
    스트림 스냅샷용으로 항상 유지."""
    cutoff = timezone.now() - timezone.timedelta(hours=24)
    latest = (
        ProductProgress.objects.values("product_id")
        .annotate(latest_id=Max("id"))
        .values("latest_id")
    )
    deleted, _ = (
        ProductProgress.objects.filter(created_at__lt=cutoff)
        .exclude(id__in=latest)
        .delete()
    )
    return {"deleted": deleted}


@shared_task(name="jobs.tasks.convert_reservations")
def convert_reservations(product_id):
    """product.rollover: 재모집 상품 오픈 시 예약분 전환."""
    from api.investments.services import convert_reservations as convert

    product = Product.objects.filter(pk=product_id).first()
    if product is None:
        return {"error": "not found"}
    return convert(product)
