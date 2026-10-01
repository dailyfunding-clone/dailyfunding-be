"""상환 스케줄 계산 (F-INV-03/08).

- 원리금균등 / 원금균등 / 만기일시
- 세금 = 이자 x 15.4% (이자소득세 14% + 지방소득세 1.4%)
- 플랫폼 이용료 = 잔여 원금 x 상품 수수료율(연) / 12
- 반올림 잔액은 마지막 회차가 흡수한다
"""
import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

TAX_RATE = Decimal("0.154")


def _won(x: Decimal) -> int:
    return int(x.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _add_months(d: date, months: int, day: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def first_pay_date(base: date, repay_day: int) -> date:
    candidate = date(base.year, base.month, min(repay_day, calendar.monthrange(base.year, base.month)[1]))
    if candidate <= base:
        candidate = _add_months(base, 1, repay_day)
    return candidate


def build_schedule(product, principal: int, base_date: date) -> list[dict]:
    """회차별 상환 계획을 dict 리스트로 반환.

    각 행: seq, pay_date, principal(회차 시작 잔액), repay_principal,
    interest_gross, tax, platform_fee, interest_net
    """
    n = product.term_months
    r = Decimal(product.annual_rate) / Decimal(1200)
    fee_r = Decimal(product.platform_fee_rate) / Decimal(1200)
    due = first_pay_date(base_date, product.repay_day)

    rows = []
    balance = principal
    if product.repay_type == product.RepayType.EQUAL_INSTALLMENT and r > 0:
        pmt = _won(
            Decimal(principal)
            * r
            * (1 + r) ** n
            / ((1 + r) ** n - 1)
        )
    else:
        pmt = None

    for seq in range(1, n + 1):
        interest_gross = _won(balance * r)
        fee = _won(balance * fee_r)
        last = seq == n

        if product.repay_type == product.RepayType.BULLET:
            repay_principal = balance if last else 0
        elif product.repay_type == product.RepayType.EQUAL_PRINCIPAL:
            repay_principal = balance if last else _won(Decimal(principal) / n)
        else:  # equal_installment
            repay_principal = balance if last or r == 0 else pmt - interest_gross
            if r == 0:
                repay_principal = balance if last else _won(Decimal(principal) / n)

        tax = _won(Decimal(interest_gross) * TAX_RATE)
        rows.append(
            {
                "seq": seq,
                "pay_date": due,
                "principal": balance,
                "repay_principal": repay_principal,
                "interest_gross": interest_gross,
                "tax": tax,
                "platform_fee": fee,
                "interest_net": interest_gross - tax - fee,
            }
        )
        balance -= repay_principal
        due = _add_months(due, 1, product.repay_day)

    assert balance == 0, "schedule principal must amortize to zero"
    return rows


def schedule_summary(product, principal: int, base_date: date):
    rows = build_schedule(product, principal, base_date)
    gross_return = sum(r["interest_gross"] for r in rows)
    net_return = sum(r["interest_net"] for r in rows)
    gross_rate = Decimal(product.annual_rate)
    net_rate = (
        gross_rate * (1 - TAX_RATE) - Decimal(product.platform_fee_rate)
    ).quantize(Decimal("0.01"))
    return {
        "gross_rate": f"{gross_rate:.2f}",
        "net_rate": f"{net_rate:.2f}",
        "gross_return": gross_return,
        "net_return": net_return,
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
