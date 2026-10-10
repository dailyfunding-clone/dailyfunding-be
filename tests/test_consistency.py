"""기획서 §5 — 6개 정합성 시나리오.

1. 모집 잔액 경합 (동시 투자 → 초과 모집 0건)
2. 중복 주문 (동일 멱등 키 → 1건)
3. 입금 웹훅 중복/위조 (서명 거부, 재수신 무시)
4. 스케줄 불일치 (스케줄 합계 = 원금, 대사 차이 0)
5. 출금 경합/실패 (강제 실패 → 잔액 복원)
6. 배치 재실행 (동일 배치 2회 → 동일 결과)
"""
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.conf import settings
from django.db import connections
from django.utils import timezone
from rest_framework.test import APIClient

from api.investments.models import Investment, RepaymentSchedule
from api.investments.services import place_investment
from api.ledger import services as ledger
from api.ledger.models import DepositIntent, Withdrawal
from api.products.models import Product
from jobs.tasks import reconcile_ledger, repay_daily
from tests.conftest import (
    deposit_webhook_payload,
    fund,
    reauth_header,
    signed_post,
)


# ---- 1. 모집 잔액 경합 ----


@pytest.mark.django_db(transaction=True)
def test_recruitment_race_no_overbooking(user, product):
    """10개의 동시 투자 요청 중 모집액을 넘는 주문은 전부 거절된다."""
    # 한도 규칙 영향 배제: 소득적격(동일차주 2,000만) + 비부동산(SCF) 상품
    from api.accounts.models import User

    user.grade = User.Grade.INCOME_ELIGIBLE
    user.save(update_fields=["grade"])
    product.type = Product.Type.SCF
    product.target_amount = 10_000_000
    product.save(update_fields=["type", "target_amount"])
    fund(user, 100_000_000)
    auth_client = APIClient()
    auth_client.force_authenticate(user=user)
    reauth = reauth_header(auth_client)

    def order(i):
        try:
            client = APIClient()
            client.force_authenticate(user=user)
            return client.post(
                "/api/investments",
                {"product_id": product.id, "amount": 5_000_000},
                format="json",
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
                **reauth,
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(order, range(10)))

    product.refresh_from_db()
    succeeded = [r for r in responses if r.status_code == 201]
    rejected = [r for r in responses if r.status_code == 409]
    # 10M 상품에 5M씩 → 정확히 2건만 성공. 나머지는 잔여 부족 또는 모집 마감.
    assert len(succeeded) == 2
    assert len(rejected) == len(responses) - 2
    assert all(
        r.json()["code"] in ("INSUFFICIENT_REMAINING", "RECRUITMENT_CLOSED")
        for r in rejected
    )
    assert product.raised_amount == product.target_amount
    assert product.status == Product.Status.RECRUITED


# ---- 2. 중복 주문 ----


@pytest.mark.django_db(transaction=True)
def test_idempotent_investment_replay(user, product):
    fund(user, 50_000_000)
    key = str(uuid.uuid4())
    client = APIClient()
    client.force_authenticate(user=user)
    body = {"product_id": product.id, "amount": 1_000_000}

    reauth = reauth_header(client)
    r1 = client.post(
        "/api/investments", body, format="json",
        HTTP_IDEMPOTENCY_KEY=key, **reauth,
    )
    r2 = client.post(
        "/api/investments", body, format="json",
        HTTP_IDEMPOTENCY_KEY=key, **reauth,
    )

    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r1.json()["investment_id"] == r2.json()["investment_id"]
    assert Investment.objects.count() == 1

    # 같은 키 + 다른 페이로드 → 409
    r3 = client.post(
        "/api/investments",
        {"product_id": product.id, "amount": 2_000_000},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
        **reauth,
    )
    assert r3.status_code == 409
    assert r3.json()["code"] == "IDEMPOTENCY_KEY_MISMATCH"
    assert Investment.objects.count() == 1

    # 키 누락 → 400
    r4 = client.post("/api/investments", body, format="json", **reauth)
    assert r4.status_code == 400
    assert r4.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"


# ---- 3. 입금 웹훅 중복/위조 ----


@pytest.mark.django_db(transaction=True)
def test_deposit_webhook_signature_and_dedup(api, user):
    va = user.virtual_account
    DepositIntent.objects.create(
        id=DepositIntent.new_id(),
        user=user,
        amount=1_000_000,
        sender_name=va.holder,
    )

    # 위조 서명 → 401
    forged = deposit_webhook_payload(va.account_no, va.holder, 1_000_000)
    r = signed_post(api, "/api/webhooks/bank/deposit", forged, secret="wrong-secret")
    assert r.status_code == 401
    assert ledger.deposit_balance(user.id) == 0

    # 오래된 타임스탬프 → 401
    r = signed_post(
        api,
        "/api/webhooks/bank/deposit",
        forged,
        ts=int(time.time()) - 1000,
    )
    assert r.status_code == 401

    # 정상 서명 → 입금 반영
    event_id = forged["event_id"]
    r = signed_post(api, "/api/webhooks/bank/deposit", forged)
    assert r.status_code == 200
    assert ledger.deposit_balance(user.id) == 1_000_000

    # 동일 event_id 재수신 → 중복 반영 없음
    r = signed_post(api, "/api/webhooks/bank/deposit", forged)
    assert r.status_code == 200
    assert r.json().get("deduplicated") is True
    assert ledger.deposit_balance(user.id) == 1_000_000


@pytest.mark.django_db(transaction=True)
def test_deposit_webhook_name_mismatch_holds(api, user):
    """예금주명 불일치 → 별도 보류 큐. 정상 intent는 오염되지 않는다."""
    va = user.virtual_account
    intent = DepositIntent.objects.create(
        id=DepositIntent.new_id(),
        user=user,
        amount=500_000,
        sender_name=va.holder,
    )
    payload = deposit_webhook_payload(va.account_no, "이상한사람", 500_000)
    r = signed_post(api, "/api/webhooks/bank/deposit", payload)
    assert r.status_code == 200
    intent.refresh_from_db()
    assert intent.status == DepositIntent.Status.PENDING
    held = DepositIntent.objects.get(
        user=user, status=DepositIntent.Status.HELD
    )
    assert held.sender_name == "이상한사람"
    assert ledger.deposit_balance(user.id) == 0


# ---- 4. 스케줄 불일치 + 대사 ----


@pytest.mark.django_db(transaction=True)
def test_schedule_sum_and_reconcile(user, product):
    fund(user, 50_000_000)
    inv, rows = place_investment(user, product.id, 3_700_000)

    # 스케줄 원금 합계 = 투자금 (반올림은 마지막 회차 흡수)
    assert sum(r["repay_principal"] for r in rows) == inv.amount
    assert inv.schedules.count() == product.term_months

    # 대사 배치 → 차이 0
    report = reconcile_ledger()
    assert report["ok"] is True
    assert report["diffs"] == []


# ---- 5. 출금 실패 → 잔액 복원 ----


def _start_transfer(wd):
    """모의 은행이 이체를 집어감: requested → processing."""
    wd.status = Withdrawal.Status.PROCESSING
    wd.save(update_fields=["status"])


@pytest.mark.django_db(transaction=True)
def test_withdrawal_failure_restores_balance(api, user):
    fund(user, 1_000_000)
    wd = ledger.create_withdrawal(user, 400_000)
    assert wd.status == Withdrawal.Status.REQUESTED
    _start_transfer(wd)
    assert ledger.deposit_balance(user.id) == 600_000
    assert ledger.held_balance(user.id) == 400_000

    # 모의 은행 실패 통지 → 역분개
    payload = {
        "event_id": f"evt-{uuid.uuid4().hex[:20]}",
        "type": "transfer.failed",
        "withdrawal_id": wd.id,
        "occurred_at": timezone.now().isoformat(),
        "reason": "계좌 오류 (모의)",
    }
    r = signed_post(api, "/api/webhooks/bank/transfer", payload)
    assert r.status_code == 200
    wd.refresh_from_db()
    assert wd.status == Withdrawal.Status.FAILED
    assert ledger.deposit_balance(user.id) == 1_000_000
    assert ledger.held_balance(user.id) == 0


@pytest.mark.django_db(transaction=True)
def test_withdrawal_success_moves_to_clearing(api, user):
    fund(user, 1_000_000)
    wd = ledger.create_withdrawal(user, 400_000)
    _start_transfer(wd)
    payload = {
        "event_id": f"evt-{uuid.uuid4().hex[:20]}",
        "type": "transfer.completed",
        "withdrawal_id": wd.id,
        "occurred_at": timezone.now().isoformat(),
    }
    r = signed_post(api, "/api/webhooks/bank/transfer", payload)
    assert r.status_code == 200
    wd.refresh_from_db()
    assert wd.status == Withdrawal.Status.COMPLETED
    assert ledger.deposit_balance(user.id) == 600_000
    assert ledger.held_balance(user.id) == 0
    assert ledger.balance(ledger.CLEARING_BANK) == 400_000 - 1_000_000


# ---- 6. 배치 재실행 멱등 ----


@pytest.mark.django_db(transaction=True)
def test_repay_batch_rerun_is_idempotent(user, product):
    fund(user, 50_000_000)
    inv, rows = place_investment(user, product.id, 2_000_000)
    product.status = Product.Status.REPAYING
    product.save(update_fields=["status"])

    first_due = rows[0]["pay_date"]
    deposit_before = ledger.deposit_balance(user.id)

    r1 = repay_daily(run_date=first_due)
    assert r1["paid"] == 1
    deposit_after = ledger.deposit_balance(user.id)
    first_net = rows[0]["repay_principal"] + rows[0]["interest_net"]
    assert deposit_after == deposit_before + first_net

    # 동일 배치 재실행 → 추가 지급 없음
    r2 = repay_daily(run_date=first_due)
    assert r2["paid"] == 0
    assert ledger.deposit_balance(user.id) == deposit_after
    assert (
        RepaymentSchedule.objects.filter(
            investment=inv, seq=1, status=RepaymentSchedule.Status.PAID
        ).count()
        == 1
    )
