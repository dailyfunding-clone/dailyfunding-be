"""상환 스케줄 수학 검증 (F-INV-03/08)."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import pytest

from api.products.models import Product
from api.products.schedule import build_schedule, first_pay_date, schedule_summary


def _product(repay_type, rate="12.00", fee="0.00", term=12, repay_day=25):
    return Product(
        product_no="t",
        name="t",
        type="scf",
        annual_rate=rate,
        term_months=term,
        target_amount=1,
        repay_type=repay_type,
        platform_fee_rate=fee,
        repay_day=repay_day,
        borrower_id="b",
    )


def test_equal_installment_amortizes():
    rows = build_schedule(
        _product(Product.RepayType.EQUAL_INSTALLMENT), 10_000_000, date(2026, 10, 1)
    )
    assert len(rows) == 12
    assert sum(r["repay_principal"] for r in rows) == 10_000_000
    # 원리금균등: 월 납입액(원금+이자)이 마지막 회차 제외 동일
    pmts = [r["repay_principal"] + r["interest_gross"] for r in rows[:-1]]
    assert len(set(pmts)) == 1
    # 잔액 감소 → 이자 감소
    interests = [r["interest_gross"] for r in rows]
    assert interests == sorted(interests, reverse=True)
    # 첫 이자 = 10M x 1%/월
    assert rows[0]["interest_gross"] == 100_000


def test_equal_principal_amortizes():
    rows = build_schedule(
        _product(Product.RepayType.EQUAL_PRINCIPAL), 9_999_999, date(2026, 10, 1)
    )
    assert sum(r["repay_principal"] for r in rows) == 9_999_999
    # 원금균등: 회차 원금이 동일하고 마지막 회차가 반올림 흡수
    principals = [r["repay_principal"] for r in rows[:-1]]
    assert len(set(principals)) == 1


def test_bullet_pays_principal_at_maturity():
    rows = build_schedule(
        _product(Product.RepayType.BULLET), 10_000_000, date(2026, 10, 1)
    )
    assert all(r["repay_principal"] == 0 for r in rows[:-1])
    assert rows[-1]["repay_principal"] == 10_000_000
    assert all(r["interest_gross"] == 100_000 for r in rows)


def test_tax_and_fee():
    # 세금 15.4%, 플랫폼 이용료 연 1.2%/12 = 잔액 x 0.1%/월
    rows = build_schedule(
        _product(Product.RepayType.BULLET, rate="10.00", fee="1.20"),
        10_000_000,
        date(2026, 10, 1),
    )
    r = rows[0]
    expected_tax = int(
        (Decimal(r["interest_gross"]) * Decimal("0.154")).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    assert r["tax"] == expected_tax
    assert r["platform_fee"] == 10_000  # 10M x 0.1%
    assert r["interest_net"] == r["interest_gross"] - r["tax"] - r["platform_fee"]


def test_first_pay_date():
    assert first_pay_date(date(2026, 10, 1), 25) == date(2026, 10, 25)
    assert first_pay_date(date(2026, 10, 25), 25) == date(2026, 11, 25)
    assert first_pay_date(date(2026, 1, 31), 25) == date(2026, 2, 25)


def test_summary_rates():
    s = schedule_summary(
        _product(Product.RepayType.BULLET, rate="10.00", fee="1.20"),
        10_000_000,
        date(2026, 10, 1),
    )
    assert s["gross_rate"] == "10.00"
    # net = 10*(1-0.154) - 1.20 = 8.46 - 1.20 = 7.26
    assert s["net_rate"] == "7.26"
    # 월 이자 83,333원(round(10M x 10%/12)) x 12회
    assert s["gross_return"] == 83_333 * 12
    assert s["net_return"] == sum(r["interest_net"] for r in s["schedule"])
