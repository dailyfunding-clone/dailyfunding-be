"""consistency/perf 수정 검증: 연체 추집, 예약 중복, ids 필터, 멱등 진행중."""
import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from api.common.idempotency import _payload_hash
from api.investments.models import RepaymentSchedule, Reservation
from api.investments.services import place_investment
from api.ledger import services as ledger
from api.ledger.models import IdempotencyRecord
from api.products.models import Product, ProductProgress
from jobs.tasks import repay_daily
from tests.conftest import fund, reauth_header


@pytest.mark.django_db(transaction=True)
def test_repay_daily_catches_up_overdue(user, product):
    """지급일 지난 미지급 회차는 다음 배치에서 추집 지급된다."""
    fund(user, 50_000_000)
    inv, rows = place_investment(user, product.id, 2_000_000)
    product.status = Product.Status.REPAYING
    product.save(update_fields=["status"])

    s = inv.schedules.get(seq=1)
    s.due_date = timezone.now().date() - timedelta(days=3)
    s.save(update_fields=["due_date"])

    deposit_before = ledger.deposit_balance(user.id)
    r = repay_daily(run_date=timezone.now().date())
    s.refresh_from_db()
    assert r["paid"] == 1
    assert s.status == RepaymentSchedule.Status.PAID
    net = s.principal + s.interest - s.tax - s.fee
    assert ledger.deposit_balance(user.id) == deposit_before + net


@pytest.mark.django_db(transaction=True)
def test_reservation_duplicate_returns_409(api, auth_api, user, product):
    fund(user, 50_000_000)
    inv, _ = place_investment(user, product.id, 1_000_000)
    body = {"investment_id": inv.id, "amount": 500_000}

    r1 = auth_api.post("/api/reservations", body, format="json")
    assert r1.status_code == 201
    r2 = auth_api.post("/api/reservations", body, format="json")
    assert r2.status_code == 409

    # DB 제약: RESERVED는 투자당 1건, 취소분은 재생성 가능
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Reservation.objects.create(investment=inv, amount=100_000)
    res = Reservation.objects.get(investment=inv)
    res.status = Reservation.Status.CANCELLED
    res.save(update_fields=["status"])
    Reservation.objects.create(investment=inv, amount=100_000)


@pytest.mark.django_db
def test_products_ids_filter(api, product):
    other = Product.objects.create(
        product_no="2026-9",
        name="다른 상품",
        type=Product.Type.SCF,
        annual_rate="8.00",
        term_months=6,
        target_amount=5_000_000,
        repay_type=Product.RepayType.BULLET,
        borrower_id="b-9",
        status=Product.Status.RECRUITING,
    )
    r = api.get(f"/api/products?ids={product.id}")
    assert r.status_code == 200
    assert [p["id"] for p in r.json()["results"]] == [product.id]

    r = api.get(f"/api/products?ids={product.id},{other.id}")
    assert r.json()["total"] == 2

    r = api.get("/api/products?ids=abc")
    assert r.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_idempotency_in_progress_code(api, user, product):
    """처리 중인 동일 키 재수신 → 409 IDEMPOTENCY_IN_PROGRESS."""
    api.force_authenticate(user=user)
    body = {"product_id": product.id, "amount": 1_000_000}
    key = str(uuid.uuid4())
    validated = {**body, "use_points": 0}
    IdempotencyRecord.objects.create(
        user=user,
        key=key,
        request_hash=_payload_hash(user.id, "/api/investments", validated),
    )
    r = api.post(
        "/api/investments",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
        **reauth_header(api),
    )
    assert r.status_code == 409
    assert r.json()["code"] == "IDEMPOTENCY_IN_PROGRESS"


@pytest.mark.django_db(transaction=True)
def test_idempotent_replay_skips_reauth(api, user, product):
    """저장된 응답 재생은 재인증 없이 200으로 반환된다."""
    fund(user, 50_000_000)
    api.force_authenticate(user=user)
    body = {"product_id": product.id, "amount": 1_000_000}
    key = str(uuid.uuid4())
    r1 = api.post(
        "/api/investments",
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
        **reauth_header(api),
    )
    assert r1.status_code == 201
    r2 = api.post(
        "/api/investments", body, format="json", HTTP_IDEMPOTENCY_KEY=key
    )
    assert r2.status_code == 200
    assert r2.json()["investment_id"] == r1.json()["investment_id"]


@pytest.mark.django_db
def test_purge_idempotency_records(user):
    old = IdempotencyRecord.objects.create(
        user=user, key="old", request_hash="x"
    )
    IdempotencyRecord.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(hours=25)
    )
    fresh = IdempotencyRecord.objects.create(
        user=user, key="fresh", request_hash="x"
    )
    from jobs.tasks import purge_idempotency_records

    purge_idempotency_records()
    assert not IdempotencyRecord.objects.filter(pk=old.pk).exists()
    assert IdempotencyRecord.objects.filter(pk=fresh.pk).exists()


@pytest.mark.django_db
def test_purge_product_progress_keeps_latest_and_fresh(product):
    rows = [
        ProductProgress.objects.create(
            product=product, raised_amount=i, remaining=100 - i, status="recruiting"
        )
        for i in range(4)
    ]
    ProductProgress.objects.exclude(pk=rows[0].pk).update(
        created_at=timezone.now() - timedelta(hours=25)
    )
    from jobs.tasks import purge_product_progress

    result = purge_product_progress()
    assert result == {"deleted": 2}
    remaining = set(ProductProgress.objects.values_list("id", flat=True))
    assert remaining == {rows[0].id, rows[3].id}
