"""운영자 도메인 로직: 대출 실행, 보류 입금 수동 매칭, 대출 신청 상품화."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from api.common.exceptions import NotFound, StateConflict, ValidationFailed
from api.investments.models import RepaymentSchedule
from api.ledger import services as ledger
from api.ledger.models import DepositIntent, LedgerEntry
from api.notifications.models import Notification, notify
from api.products.models import Product
from api.products.schedule import first_pay_date


def transition_product(product: Product, to_status: str) -> Product:
    product.transition(to_status)
    if to_status == Product.Status.RECRUITING:
        product.recruit_open_at = timezone.now()
    product.save(update_fields=["status", "recruit_open_at"])

    if to_status == Product.Status.RECRUITING:
        from api.accounts.models import User

        notify(
            User.objects.filter(is_active=True).values_list("id", flat=True),
            Notification.Kind.NEW_PRODUCT,
            f"신규 상품 오픈: {product.name}",
            ref_id=product.id,
        )
        if product.refinance_of_id:
            from api.investments.services import convert_reservations

            convert_reservations(product)
    elif to_status == Product.Status.RECRUITED:
        notify(
            product.investments.values_list("user_id", flat=True).distinct(),
            Notification.Kind.RECRUIT_CLOSED,
            f"모집 완료: {product.name}",
            ref_id=product.id,
        )
    return product


@transaction.atomic
def execute_loan(product: Product) -> Product:
    """F-ADM-02: 모집완료 → 대출 실행. 투자금을 차입자 계정으로 이동하고
    상환 스케줄 지급일을 실행일 기준으로 확정한다."""
    product = Product.objects.select_for_update().get(pk=product.pk)
    if product.status != Product.Status.RECRUITED:
        raise StateConflict(
            "product must be recruited to execute",
            {"status": product.status},
        )
    total = product.raised_amount
    ledger.post(
        LedgerEntry.Kind.LOAN_EXECUTE,
        [
            (ledger.investment_acc(product.id), -total),
            (ledger.borrower_acc(product.id), total),
        ],
        ref_type="product",
        ref_id=product.id,
    )
    product.transition(Product.Status.EXECUTED)
    product.executed_at = timezone.now()
    product.transition(Product.Status.REPAYING)
    product.save(update_fields=["status", "executed_at"])

    # 실행일 기준으로 지급일 확정
    first = first_pay_date(product.executed_at.date(), product.repay_day)
    schedules = RepaymentSchedule.objects.filter(
        investment__product=product
    ).order_by("investment_id", "seq")
    for s in schedules:
        s.due_date = _shift_months(first, s.seq - 1)
    RepaymentSchedule.objects.bulk_update(schedules, ["due_date"])
    return product


def _shift_months(d, months):
    from api.products.schedule import _add_months

    return _add_months(d, months, d.day)


@transaction.atomic
def match_held_deposit(intent_id, admin):
    """F-ADM-06: 보류 입금 수동 매칭 → 입금 확정."""
    intent = DepositIntent.objects.select_for_update().filter(pk=intent_id).first()
    if intent is None:
        raise NotFound("intent not found")
    if intent.status != DepositIntent.Status.HELD:
        raise StateConflict(
            "intent is not held", {"status": intent.status}
        )
    ledger.credit_deposit(intent)
    return intent
