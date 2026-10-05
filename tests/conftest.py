import json
import time
import uuid
from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from api.accounts.models import User
from api.accounts.services import create_user_account, verify_identity
from api.investments.models import SuitabilityTest
from api.ledger import services as ledger
from api.ledger.models import DepositIntent
from api.products.models import Product
from api.webhooks.views import sign_payload

AGREEMENTS = [
    {"term": t, "agreed": True}
    for t in [
        "investment",
        "service",
        "privacy",
        "credit_info",
        "electronic_finance",
    ]
]

PASSWORD = "Test1234!"


def _make_user(email, name, birth, phone, carrier="SKT"):
    u = create_user_account(email, PASSWORD, User.Role.INVESTOR, AGREEMENTS, name=name)
    verify_identity(u, carrier, name, birth, phone)
    SuitabilityTest.objects.create(
        user=u,
        passed=True,
        passed_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=365),
    )
    return u


@pytest.fixture
def user(db):
    return _make_user("investor@test.local", "홍길동", "19900101", "01012345678")


@pytest.fixture
def other_user(db):
    return _make_user(
        "investor2@test.local", "김철수", "19920202", "01098765432", carrier="KT"
    )


@pytest.fixture
def staff(db):
    return User.objects.create_superuser("admin@test.local", PASSWORD, name="운영자")


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def auth_api(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


@pytest.fixture
def staff_api(staff):
    c = APIClient()
    c.force_authenticate(user=staff)
    return c


@pytest.fixture
def product(db):
    return Product.objects.create(
        product_no="2026-1",
        name="아파트 담보대출 1호",
        type=Product.Type.MORTGAGE,
        annual_rate="9.80",
        term_months=12,
        target_amount=10_000_000,
        raised_amount=0,
        repay_type=Product.RepayType.EQUAL_INSTALLMENT,
        platform_fee_rate="1.20",
        repay_day=25,
        borrower_id="borrower-1",
        borrower_name="차주1",
        status=Product.Status.RECRUITING,
    )


def fund(user, amount):
    """테스트용 예치금 직접 충전 (원장 분개 경유)."""
    intent = DepositIntent.objects.create(
        id=DepositIntent.new_id(),
        user=user,
        amount=amount,
        sender_name=user.name,
        status=DepositIntent.Status.PENDING,
    )
    ledger.credit_deposit(intent)
    return intent


def deposit_webhook_payload(account_no, sender, amount, event_id=None):
    return {
        "event_id": event_id or f"evt-{uuid.uuid4().hex[:20]}",
        "type": "deposit.completed",
        "account_no": account_no,
        "sender_name": sender,
        "amount": amount,
        "occurred_at": "2026-10-02T00:00:00Z",
    }


def signed_post(client, path, payload, secret=None, ts=None):
    """HMAC 서명을 붙여 웹훅 엔드포인트로 POST."""
    body = json.dumps(payload).encode()
    ts = ts or int(time.time())
    secret = secret or settings.BANK_WEBHOOK_SECRET
    sig = f"t={ts},v1={sign_payload(body, ts, secret)}"
    return client.post(
        path,
        data=body,
        content_type="application/json",
        HTTP_X_BANK_SIGNATURE=sig,
    )
