"""포인트 원장·소멸·PIN 잠금·장바구니 테스트."""
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from api.ledger import services as ledger
from api.ledger.models import PointEntry
from api.products.models import Product
from tests.conftest import PASSWORD, fund, reauth_header


@pytest.mark.django_db
def test_point_earn_spend_expire(auth_api, user):
    ledger.grant_points(user, 10_000, memo="테스트 적립")
    assert ledger.point_balance(user.id) == 10_000

    # 전환 → 예치금
    r = auth_api.post(
        "/api/points/convert",
        {"amount": 4_000},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert r.status_code == 200
    assert ledger.point_balance(user.id) == 6_000
    assert ledger.deposit_balance(user.id) == 4_000

    # 소멸 배치
    earn = PointEntry.objects.get(user=user, kind="earn")
    earn.expires_at = timezone.now() - timedelta(days=1)
    earn.save()
    count = ledger.expire_points()
    assert count == 1
    assert ledger.point_balance(user.id) == 0

    # 재실행 멱등
    assert ledger.expire_points() == 0


@pytest.mark.django_db
def test_point_use_in_investment(auth_api, user, product):
    fund(user, 4_500_000)
    ledger.grant_points(user, 500_000)
    r = auth_api.post(
        "/api/investments",
        {"product_id": product.id, "amount": 5_000_000, "use_points": 500_000},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        **reauth_header(auth_api),
    )
    assert r.status_code == 201
    assert r.json()["points_used"] == 500_000
    assert ledger.deposit_balance(user.id) == 0
    assert ledger.point_balance(user.id) == 0


@pytest.mark.django_db
def test_pin_lock_after_5_failures(api, user):
    api.post(
        "/api/auth/login", {"email": user.email, "password": PASSWORD}, format="json"
    )
    api.force_authenticate(user=user)
    api.post("/api/auth/pin", {"pin": "123456"}, format="json")

    for _ in range(4):
        r = api.post("/api/auth/login/pin", {"pin": "999999"}, format="json")
        assert r.status_code == 401
    r = api.post("/api/auth/login/pin", {"pin": "999999"}, format="json")
    assert r.status_code == 422
    assert r.json()["code"] == "PIN_LOCKED"

    # 잠금 후 올바른 PIN도 거부
    r = api.post("/api/auth/login/pin", {"pin": "123456"}, format="json")
    assert r.status_code == 422


@pytest.mark.django_db
def test_cart_crud(auth_api, product):
    r = auth_api.post("/api/cart", {"product_id": product.id}, format="json")
    assert r.status_code == 201
    r = auth_api.get("/api/cart")
    assert r.json()["count"] == 1
    cart_id = r.json()["results"][0]["id"]
    r = auth_api.delete(f"/api/cart/{cart_id}")
    assert r.status_code == 204


@pytest.mark.django_db
def test_withdraw_requires_reauth(auth_api, user):
    fund(user, 1_000_000)
    r = auth_api.post(
        "/api/deposit/withdraw",
        {"amount": 100_000},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert r.status_code == 401
    assert r.json()["code"] == "REAUTH_REQUIRED"
