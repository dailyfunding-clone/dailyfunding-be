"""API 레벨 테스트: 인증·투자 주문 검증·한도 규칙·E2E."""
import json
import time
import uuid

import pytest
from django.conf import settings
from django.utils import timezone

from api.accounts.models import User
from api.accounts.services import issue_virtual_account
from api.investments.models import Investment
from api.ledger import services as ledger
from api.ledger.models import DepositIntent, Withdrawal
from api.products.models import Product
from api.webhooks.views import sign_payload
from tests.conftest import (
    AGREEMENTS,
    PASSWORD,
    deposit_webhook_payload,
    fund,
    reauth_header,
    signed_post,
)


def _signup(api, email="new@test.local"):
    return api.post(
        "/api/auth/signup",
        {
            "email": email,
            "password": PASSWORD,
            "agreements": AGREEMENTS,
        },
        format="json",
    )


@pytest.mark.django_db
def test_signup_creates_virtual_account(api):
    r = _signup(api)
    assert r.status_code == 201
    user = User.objects.get(email="new@test.local")
    assert user.virtual_account.account_no.startswith("100-")
    assert user.role == User.Role.INVESTOR

    # 중복 이메일 → 409 EMAIL_TAKEN
    r2 = _signup(api)
    assert r2.status_code == 409
    assert r2.json()["code"] == "EMAIL_TAKEN"


@pytest.mark.django_db
def test_signup_password_policy(api):
    r = api.post(
        "/api/auth/signup",
        {"email": "x@test.local", "password": "weak", "agreements": AGREEMENTS},
        format="json",
    )
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.django_db
def test_login_sets_cookies(api, user):
    r = api.post(
        "/api/auth/login", {"email": user.email, "password": PASSWORD}, format="json"
    )
    assert r.status_code == 200
    assert "access" in r.cookies and "refresh" in r.cookies
    assert r.json()["grade"] == "general"


@pytest.mark.django_db
def test_invest_requires_reauth(auth_api, product):
    """민감 동작: X-Reauth-Token 없는 투자 주문은 401."""
    r = auth_api.post(
        "/api/investments",
        {"product_id": product.id, "amount": 1_000_000},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert r.status_code == 401
    assert r.json()["code"] == "REAUTH_REQUIRED"


@pytest.mark.django_db
def test_invest_requires_suitability(api, product):
    # 적합성 미통과 사용자
    u = User.objects.create_user("bare@test.local", PASSWORD)
    issue_virtual_account(u)
    fund(u, 10_000_000)
    api.force_authenticate(user=u)
    r = api.post(
        "/api/investments",
        {"product_id": product.id, "amount": 1_000_000},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        **reauth_header(api),
    )
    assert r.status_code == 403
    assert r.json()["code"] == "SUITABILITY_REQUIRED"


@pytest.mark.django_db
def test_invest_insufficient_deposit(auth_api, product):
    r = auth_api.post(
        "/api/investments",
        {"product_id": product.id, "amount": 1_000_000},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        **reauth_header(auth_api),
    )
    assert r.status_code == 409
    assert r.json()["code"] == "INSUFFICIENT_DEPOSIT"
    assert r.json()["details"]["available"] == 0


@pytest.mark.django_db
def test_invest_borrower_limit(auth_api, user, product):
    """일반 등급 동일차주 한도 500만원."""
    fund(user, 50_000_000)
    r1 = auth_api.post(
        "/api/investments",
        {"product_id": product.id, "amount": 5_000_000},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        **reauth_header(auth_api),
    )
    assert r1.status_code == 201

    # 같은 차주의 다른 상품에 추가 투자 → 동일차주 한도 초과
    p2 = Product.objects.create(
        product_no="2026-2",
        name="상가 담보대출 2호",
        type=Product.Type.MORTGAGE,
        annual_rate="9.00",
        term_months=6,
        target_amount=50_000_000,
        repay_type=Product.RepayType.BULLET,
        borrower_id="borrower-1",  # 동일 차주
        status=Product.Status.RECRUITING,
    )
    r2 = auth_api.post(
        "/api/investments",
        {"product_id": p2.id, "amount": 1_000_000},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        **reauth_header(auth_api),
    )
    assert r2.status_code == 403
    assert r2.json()["code"] == "BORROWER_LIMIT_EXCEEDED"
    assert r2.json()["details"]["remaining_limit"] == 0


@pytest.mark.django_db
def test_invest_total_grade_limit(auth_api, user, product):
    """일반 등급 총 한도 4,000만원."""
    fund(user, 100_000_000)
    p2 = Product.objects.create(
        product_no="2026-3",
        name="매출채권 3호",
        type=Product.Type.SCF,
        annual_rate="8.00",
        term_months=6,
        target_amount=100_000_000,
        repay_type=Product.RepayType.BULLET,
        borrower_id="borrower-9",
        status=Product.Status.RECRUITING,
    )
    r = auth_api.post(
        "/api/investments",
        {"product_id": p2.id, "amount": 41_000_000},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        **reauth_header(auth_api),
    )
    assert r.status_code == 403
    assert r.json()["code"] == "GRADE_LIMIT_EXCEEDED"
    assert r.json()["details"]["remaining_limit"] == 40_000_000


@pytest.mark.django_db
def test_invest_closed_product(auth_api, user, product):
    fund(user, 10_000_000)
    product.status = Product.Status.REPAID
    product.save()
    r = auth_api.post(
        "/api/investments",
        {"product_id": product.id, "amount": 1_000_000},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        **reauth_header(auth_api),
    )
    assert r.status_code == 409
    assert r.json()["code"] == "RECRUITMENT_CLOSED"


@pytest.mark.django_db
def test_invest_recruited_on_full(auth_api, user, product):
    fund(user, 10_000_000)
    # 한도 회피를 위해 전문 등급으로
    user.grade = User.Grade.PROFESSIONAL
    user.save()
    r = auth_api.post(
        "/api/investments",
        {"product_id": product.id, "amount": 4_000_000},  # 전문 40% 상한 내
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        **reauth_header(auth_api),
    )
    assert r.status_code == 201
    product.refresh_from_db()
    assert product.raised_amount == 4_000_000
    assert product.status == Product.Status.RECRUITING


@pytest.mark.django_db
def test_suitability_flow(auth_api, user):
    r = auth_api.get("/api/suitability-test")
    assert r.status_code == 200
    assert len(r.json()["questions"]) == 6
    assert "answer" not in r.json()["questions"][0]

    # 전부 오답 → 불합격
    r = auth_api.post(
        "/api/suitability-test",
        {"answers": [{"seq": i, "choice": "O"} for i in range(1, 7)]},
        format="json",
    )
    assert r.json()["passed"] is False

    # 정답 → 합격 + 1년 유효
    from api.investments.services import SUITABILITY_QUESTIONS

    r = auth_api.post(
        "/api/suitability-test",
        {
            "answers": [
                {"seq": q["seq"], "choice": q["answer"]}
                for q in SUITABILITY_QUESTIONS
            ]
        },
        format="json",
    )
    assert r.json()["passed"] is True
    assert r.json()["expires_at"] is not None


@pytest.mark.django_db
def test_products_list_and_detail(api, auth_api, product):
    r = api.get("/api/products")
    assert r.status_code == 200
    assert r.json()["total"] == 1

    r = api.get("/api/products?type=stock_loan")
    assert r.json()["total"] == 0

    r = api.get(f"/api/products/{product.id}")
    assert r.json()["remaining_amount"] == 10_000_000
    assert "tabs" in r.json()

    # 로그인 시 개인화 블록
    r = auth_api.get(f"/api/products/{product.id}")
    assert r.json()["my"]["grade_remaining_limit"] == 40_000_000

    # 스케줄 프리뷰
    r = api.get(f"/api/products/{product.id}/schedule-preview?amount=1000000")
    assert r.status_code == 200
    assert len(r.json()["schedule"]) == 12


@pytest.mark.django_db
def test_e2e_lifecycle(api, staff_api, user):
    """가입→입금(웹훅)→투자→실행→상환→출금 E2E를 API만으로 완주."""
    # 입금 알리기 → 모의 은행 웹훅
    api.force_authenticate(user=user)
    r = api.post(
        "/api/deposit/notify-intent",
        {"sender_name": user.name, "amount": 5_000_000},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert r.status_code == 202
    intent_id = r.json()["intent_id"]

    va = user.virtual_account
    payload = deposit_webhook_payload(va.account_no, va.holder, 5_000_000)
    r = signed_post(api, "/api/webhooks/bank/deposit", payload)
    assert r.status_code == 200
    assert ledger.deposit_balance(user.id) == 5_000_000

    # 운영자가 상품 생성 → 모집 오픈
    r = staff_api.post(
        "/api/admin/products",
        {
            "name": "E2E 상품",
            "type": "scf",
            "annual_rate": "10.00",
            "term_months": 3,
            "target_amount": 5_000_000,
            "repay_type": "bullet",
            "borrower_id": "b-e2e",
            "platform_fee_rate": "0.00",
        },
        format="json",
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    staff_api.patch(f"/api/admin/products/{pid}/status", {"status": "scheduled"}, format="json")
    staff_api.patch(f"/api/admin/products/{pid}/status", {"status": "recruiting"}, format="json")

    # 투자 (전액 → recruited)
    r = api.post(
        "/api/investments",
        {"product_id": pid, "amount": 5_000_000},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        **reauth_header(api),
    )
    assert r.status_code == 201
    inv_id = r.json()["investment_id"]
    assert ledger.deposit_balance(user.id) == 0

    # 대출 실행
    r = staff_api.post(f"/api/admin/products/{pid}/execute")
    assert r.json()["status"] == "repaying"

    # 상환 배치: 회차별 지급일에 시간 진행
    inv = Investment.objects.get(pk=inv_id)
    for s in inv.schedules.order_by("seq"):
        r = staff_api.post(f"/api/admin/batch/repay?date={s.due_date}")
    inv.refresh_from_db()
    assert inv.status == "repaid"
    final_deposit = ledger.deposit_balance(user.id)
    assert final_deposit > 5_000_000  # 원금 + 세후 이자

    # 출금 → 모의 은행 이체 완료 웹훅
    r = api.post("/api/auth/reauth", {"password": PASSWORD}, format="json")
    reauth = r.json()["reauth_token"]
    r = api.post(
        "/api/deposit/withdraw",
        {"amount": 1_000_000},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        HTTP_X_REAUTH_TOKEN=reauth,
    )
    assert r.status_code == 202
    wd_id = r.json()["withdrawal_id"]

    wd = Withdrawal.objects.get(id=wd_id)
    wd.status = Withdrawal.Status.PROCESSING
    wd.save()
    payload = {
        "event_id": f"evt-{uuid.uuid4().hex[:20]}",
        "type": "transfer.completed",
        "withdrawal_id": wd_id,
        "occurred_at": timezone.now().isoformat(),
    }
    r = signed_post(api, "/api/webhooks/bank/transfer", payload)
    assert r.status_code == 200
    assert ledger.deposit_balance(user.id) == final_deposit - 1_000_000

    # 대사: 차이 0
    r = staff_api.post("/api/admin/batch/reconcile")
    assert r.json()["ok"] is True
