"""투자 주문 (F-INV-04) + 상환 스케줄 생성 + 적합성 테스트."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from api.accounts.models import GRADE_LIMITS, REAL_ESTATE_TYPES, User
from api.common.exceptions import (
    BorrowerLimitExceeded,
    GradeLimitExceeded,
    InsufficientDeposit,
    InsufficientRemaining,
    RecruitmentClosed,
    StateConflict,
    SuitabilityRequired,
    ValidationFailed,
)
from api.investments.models import (
    Investment,
    RepaymentSchedule,
    Reservation,
    SuitabilityTest,
)
from api.ledger import services as ledger
from api.ledger.models import LedgerEntry
from api.products.models import Product
from api.products.schedule import build_schedule

# F-INV-05 적합성 6문항 (온투업 협회 지침 모사). answer는 서버만 안다.
SUITABILITY_QUESTIONS = [
    {
        "seq": 1,
        "text": "온투업 투자상품은 예금자보호 대상이다.",
        "answer_options": ["O", "X"],
        "answer": "X",
    },
    {
        "seq": 2,
        "text": "온투업 투자는 원금 손실이 발생할 수 있다.",
        "answer_options": ["O", "X"],
        "answer": "O",
    },
    {
        "seq": 3,
        "text": "투자 수익에는 이자소득세 등 세금이 부과된다.",
        "answer_options": ["O", "X"],
        "answer": "O",
    },
    {
        "seq": 4,
        "text": "온투업 회사가 투자 원금과 수익을 보장한다.",
        "answer_options": ["O", "X"],
        "answer": "X",
    },
    {
        "seq": 5,
        "text": "투자자 등급에 따라 투자 한도가 달라진다.",
        "answer_options": ["O", "X"],
        "answer": "O",
    },
    {
        "seq": 6,
        "text": "중도 상환 시 예상 수익이 줄어들 수 있다.",
        "answer_options": ["O", "X"],
        "answer": "O",
    },
]


def suitability_valid(user) -> bool:
    return SuitabilityTest.objects.filter(
        user=user, passed=True, expires_at__gt=timezone.now()
    ).exists()


def grade_suitability(user, answers):
    """서버 채점. answers = [{seq, choice}]."""
    by_seq = {q["seq"]: q["answer"] for q in SUITABILITY_QUESTIONS}
    passed = len(answers) == len(by_seq) and all(
        by_seq.get(a.get("seq")) == a.get("choice") for a in answers
    )
    now = timezone.now()
    record = SuitabilityTest.objects.create(
        user=user,
        passed=passed,
        passed_at=now if passed else None,
        expires_at=now + timedelta(days=365) if passed else None,
    )
    return record


def invested_sums(user):
    """현재 투자 중(연체 포함) 원금 합계를 용도별로."""
    active = Investment.objects.filter(
        user=user, status__in=[Investment.Status.ACTIVE, Investment.Status.OVERDUE]
    ).select_related("product")
    total = 0
    real_estate = 0
    by_borrower = {}
    for inv in active:
        total += inv.amount
        if inv.product.type in REAL_ESTATE_TYPES:
            real_estate += inv.amount
        by_borrower[inv.product.borrower_id] = (
            by_borrower.get(inv.product.borrower_id, 0) + inv.amount
        )
    return total, real_estate, by_borrower


def create_schedules(investment: Investment, product: Product, base_date):
    rows = build_schedule(product, investment.amount, base_date)
    RepaymentSchedule.objects.bulk_create(
        [
            RepaymentSchedule(
                investment=investment,
                seq=r["seq"],
                due_date=r["pay_date"],
                principal_balance=r["principal"],
                principal=r["repay_principal"],
                interest=r["interest_gross"],
                fee=r["platform_fee"],
                tax=r["tax"],
            )
            for r in rows
        ]
    )
    return rows


@transaction.atomic
def place_investment(user: User, product_id: int, amount: int, use_points: int = 0):
    """단일 트랜잭션 + 상품 행 잠금으로 모집 잔액 경합을 직렬화한다."""
    ledger.lock_user(user.id)
    product = Product.objects.select_for_update().filter(pk=product_id).first()
    if product is None:
        raise ValidationFailed("product not found", {"product_id": "invalid"})

    if product.status != Product.Status.RECRUITING:
        raise RecruitmentClosed()

    if not suitability_valid(user):
        raise SuitabilityRequired()

    if amount <= 0:
        raise ValidationFailed("amount must be positive")
    if use_points < 0:
        raise ValidationFailed("use_points must be >= 0")

    limits = GRADE_LIMITS[user.grade]
    total, real_estate, by_borrower = invested_sums(user)

    # 등급 총 한도
    if limits["total"] is not None and total + amount > limits["total"]:
        raise GradeLimitExceeded(
            details={"remaining_limit": max(0, limits["total"] - total)}
        )
    # 부동산 한도
    if (
        product.type in REAL_ESTATE_TYPES
        and limits["real_estate"] is not None
        and real_estate + amount > limits["real_estate"]
    ):
        raise GradeLimitExceeded(
            "real-estate limit exceeded",
            {"remaining_limit": max(0, limits["real_estate"] - real_estate)},
        )
    # 동일차주 한도
    same_borrower = by_borrower.get(product.borrower_id, 0)
    if (
        limits["same_borrower"] is not None
        and same_borrower + amount > limits["same_borrower"]
    ):
        raise BorrowerLimitExceeded(
            details={
                "remaining_limit": max(0, limits["same_borrower"] - same_borrower)
            }
        )
    # 전문투자자 상품별 40% 상한
    if limits["per_product_pct"] is not None:
        cap = int(Decimal(product.target_amount) * Decimal(limits["per_product_pct"]))
        mine = sum(
            i.amount
            for i in Investment.objects.filter(
                user=user,
                product=product,
                status__in=[Investment.Status.ACTIVE, Investment.Status.OVERDUE],
            )
        )
        if mine + amount > cap:
            raise GradeLimitExceeded(
                "per-product limit exceeded",
                {"remaining_limit": max(0, cap - mine)},
            )

    # 잔여 모집액 (행 잠금 아래에서 판정)
    if amount > product.remaining_amount:
        raise InsufficientRemaining(
            details={"remaining": product.remaining_amount}
        )

    # 예치금 + 포인트
    deposit = ledger.deposit_balance(user.id)
    points = ledger.point_balance(user.id)
    if use_points > points:
        raise ValidationFailed(
            "use_points exceeds balance", {"points": points}
        )
    if deposit + use_points < amount:
        raise InsufficientDeposit(
            details={"available": deposit + use_points}
        )

    investment = Investment.objects.create(
        user=user,
        product=product,
        amount=amount,
        points_used=use_points,
    )

    # 분개: 예치금 차감 + 포인트 사용 → 상품 에스크로
    postings = [
        (ledger.deposit_acc(user.id), -(amount - use_points)),
        (ledger.investment_acc(product.id), amount),
    ]
    if use_points:
        postings.append((ledger.POINT_LIABILITY, -use_points))
        ledger._consume_earns(
            user.id, use_points, "spend", "investment", investment.id, "투자 사용"
        )
    ledger.post(
        LedgerEntry.Kind.INVEST,
        postings,
        ref_type="investment",
        ref_id=investment.id,
    )

    rows = create_schedules(investment, product, timezone.now().date())
    investment.expected_net_return = sum(r["interest_net"] for r in rows)
    investment.save(update_fields=["expected_net_return"])

    product.raised_amount += amount
    if product.raised_amount >= product.target_amount:
        product.status = Product.Status.RECRUITED
    product.save(update_fields=["raised_amount", "status"])

    return investment, rows


def invest_response(investment: Investment, rows) -> dict:
    return {
        "investment_id": investment.id,
        "amount": investment.amount,
        "points_used": investment.points_used,
        "expected_net_return": investment.expected_net_return,
        "status": investment.status,
        "schedule": [
            {
                "seq": r["seq"],
                "pay_date": r["pay_date"].isoformat(),
                "principal": r["principal"],
                "repay_principal": r["repay_principal"],
                "interest_gross": r["interest_gross"],
                "tax": r["tax"],
                "platform_fee": r["platform_fee"],
                "interest_net": r["interest_net"],
            }
            for r in rows
        ],
    }


def invest_response_from_db(investment: Investment) -> dict:
    rows = [
        {
            "seq": s.seq,
            "pay_date": s.due_date.isoformat(),
            "principal": s.principal_balance,
            "repay_principal": s.principal,
            "interest_gross": s.interest,
            "tax": s.tax,
            "platform_fee": s.fee,
            "interest_net": s.interest_net,
        }
        for s in investment.schedules.all()
    ]
    return {
        "investment_id": investment.id,
        "amount": investment.amount,
        "points_used": investment.points_used,
        "expected_net_return": investment.expected_net_return,
        "status": investment.status,
        "schedule": rows,
    }


def convert_reservations(product: Product):
    """product.rollover: refinance_of 상품의 예약을 신규 상품 투자로 전환."""
    converted, refunded = 0, 0
    reservations = Reservation.objects.filter(
        status=Reservation.Status.RESERVED,
        investment__product_id=product.refinance_of_id,
    ).select_related("investment__user")
    for res in reservations:
        user = res.investment.user
        try:
            inv, _ = place_investment(user, product.id, res.amount)
            res.status = Reservation.Status.CONVERTED
            res.converted_at = timezone.now()
            converted += 1
        except Exception:
            # 모집 미달·한도 초과 등 → 원리금 상환으로 폴백
            res.status = Reservation.Status.REFUNDED
            refunded += 1
        res.save(update_fields=["status", "converted_at"])
    return {"converted": converted, "refunded": refunded}
