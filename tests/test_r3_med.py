import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from api.accounts import services as account_services
from api.accounts.models import (
    AppLoginCode,
    GradeRequest,
    PasswordResetToken,
    ReauthToken,
    User,
)
from api.common.exceptions import ValidationFailed
from api.investments import services as invest_services
from api.investments.models import Investment, RepaymentSchedule, Reservation
from api.ledger import services as ledger
from api.ledger.models import DepositIntent, Withdrawal
from api.products.models import Product
from tests.conftest import (
    AGREEMENTS,
    PASSWORD,
    deposit_webhook_payload,
    fund,
    reauth_header,
    signed_post,
)


# ---- 웹훅 type 검증 + REQUESTED 교착 ----


@pytest.mark.django_db(transaction=True)
def test_deposit_webhook_rejects_wrong_type(api, user):
    va = user.virtual_account
    payload = deposit_webhook_payload(va.account_no, va.holder, 100_000)
    payload["type"] = "transfer.completed"
    r = signed_post(api, "/api/webhooks/bank/deposit", payload)
    assert r.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_transfer_webhook_rejects_wrong_type(api, user):
    fund(user, 100_000)
    wd = ledger.create_withdrawal(user, 50_000)
    payload = {
        "event_id": f"evt-{uuid.uuid4().hex[:20]}",
        "type": "deposit.completed",
        "withdrawal_id": wd.id,
        "occurred_at": timezone.now().isoformat(),
    }
    r = signed_post(api, "/api/webhooks/bank/transfer", payload)
    assert r.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_transfer_webhook_completes_requested_withdrawal(api, user):
    """REQUESTED 상태에 결과 웹훅이 먼저 도착해도 교착되지 않는다."""
    fund(user, 100_000)
    wd = ledger.create_withdrawal(user, 50_000)
    assert wd.status == Withdrawal.Status.REQUESTED
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
    assert ledger.held_balance(user.id) == 0


@pytest.mark.django_db(transaction=True)
def test_transfer_webhook_fails_requested_withdrawal(api, user):
    fund(user, 100_000)
    wd = ledger.create_withdrawal(user, 50_000)
    payload = {
        "event_id": f"evt-{uuid.uuid4().hex[:20]}",
        "type": "transfer.failed",
        "withdrawal_id": wd.id,
        "occurred_at": timezone.now().isoformat(),
        "reason": "계좌 오류",
    }
    r = signed_post(api, "/api/webhooks/bank/transfer", payload)
    assert r.status_code == 200
    wd.refresh_from_db()
    assert wd.status == Withdrawal.Status.FAILED
    assert ledger.deposit_balance(user.id) == 100_000


# ---- HELD intent 오염 ----


@pytest.mark.django_db(transaction=True)
def test_sender_mismatch_does_not_hold_matching_pending_intent(api, user):
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
    assert held.id != intent.id


# ---- use_points > amount + 본인인증 게이트 ----


@pytest.mark.django_db(transaction=True)
def test_use_points_exceeding_amount_rejected(user, product):
    fund(user, 10_000_000)
    ledger.grant_points(user, 500_000, ref_type="test", ref_id="1")
    with pytest.raises(ValidationFailed):
        invest_services.place_investment(user, product.id, 100_000, 200_000)


@pytest.mark.django_db(transaction=True)
def test_place_investment_requires_identity(product, db):
    u = account_services.create_user_account(
        "noid@test.local", PASSWORD, User.Role.INVESTOR, AGREEMENTS, name="무명"
    )
    from api.investments.models import SuitabilityTest

    SuitabilityTest.objects.create(
        user=u,
        passed=True,
        passed_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=365),
    )
    fund(u, 10_000_000)
    with pytest.raises(ValidationFailed):
        invest_services.place_investment(u, product.id, 100_000)


# ---- app-code ----


@pytest.mark.django_db(transaction=True)
def test_app_code_exchange_single_use(api, user):
    from rest_framework.test import APIClient

    code = account_services.issue_app_code(user)
    r = api.post("/api/auth/app-code/exchange", {"code": code.code}, format="json")
    assert r.status_code == 200
    r = APIClient().post(
        "/api/auth/app-code/exchange", {"code": code.code}, format="json"
    )
    assert r.status_code == 401


@pytest.mark.django_db(transaction=True)
def test_app_code_issue_retries_on_collision(user, monkeypatch):
    AppLoginCode.objects.create(
        user=user,
        code="123456",
        expires_at=timezone.now() + timedelta(seconds=60),
    )
    codes = iter([123456, 654321])
    monkeypatch.setattr(
        account_services.secrets, "randbelow", lambda n: next(codes)
    )
    code = account_services.issue_app_code(user)
    assert code.code == "654321"


# ---- check-then-create → 409 ----


@pytest.mark.django_db(transaction=True)
def test_signup_email_race_returns_409(api, user, monkeypatch):
    from django.db.models.query import QuerySet

    monkeypatch.setattr(QuerySet, "exists", lambda self: False)
    r = api.post(
        "/api/auth/signup",
        {
            "email": user.email,
            "password": PASSWORD,
            "agreements": AGREEMENTS,
        },
        format="json",
    )
    assert r.status_code == 409
    assert r.json()["code"] == "EMAIL_TAKEN"


@pytest.mark.django_db(transaction=True)
def test_verify_identity_ci_race_returns_409(user, monkeypatch):
    from django.db.models.query import QuerySet

    other = account_services.create_user_account(
        "ci-race@test.local", PASSWORD, User.Role.INVESTOR, AGREEMENTS, name="타인"
    )
    account_services.verify_identity(other, "SKT", "동명", "19900101", "01099998888")
    monkeypatch.setattr(QuerySet, "exists", lambda self: False)
    with pytest.raises(Exception) as exc:
        account_services.verify_identity(
            user, "SKT", "동명", "19900101", "01099998888"
        )
    assert exc.value.status_code == 409


# ---- DRAFT 공개 차단 ----


@pytest.fixture
def draft_product(db):
    return Product.objects.create(
        product_no="2026-draft",
        name="초안 상품",
        type=Product.Type.SCF,
        annual_rate="8.00",
        term_months=6,
        target_amount=5_000_000,
        repay_type=Product.RepayType.BULLET,
        borrower_id="borrower-9",
        status=Product.Status.DRAFT,
    )


@pytest.mark.django_db
def test_draft_hidden_from_public_list(api, product, draft_product):
    r = api.get("/api/products")
    assert all(row["id"] != draft_product.id for row in r.json()["results"])
    r = api.get("/api/products?include_closed=true")
    assert all(row["id"] != draft_product.id for row in r.json()["results"])
    r = api.get("/api/products?status=draft")
    assert r.json()["results"] == []


@pytest.mark.django_db
def test_draft_detail_and_preview_404(api, draft_product):
    assert api.get(f"/api/products/{draft_product.id}").status_code == 404
    assert (
        api.get(
            f"/api/products/{draft_product.id}/schedule-preview?amount=1000"
        ).status_code
        == 404
    )


# ---- 캐스트 가드 ----


@pytest.mark.django_db
def test_investments_invalid_page_400(auth_api):
    assert auth_api.get("/api/investments?page=abc").status_code == 400
    assert auth_api.get("/api/investments?page_size=xyz").status_code == 400


@pytest.mark.django_db
def test_deposit_history_bad_params_400(auth_api):
    assert auth_api.get("/api/deposit/history?cursor=abc").status_code == 400
    assert auth_api.get("/api/deposit/history?from=notadate").status_code == 400
    assert auth_api.get("/api/points/history?to=notadate").status_code == 400


@pytest.mark.django_db
def test_admin_batch_invalid_date_400(staff_api):
    assert staff_api.post("/api/admin/batch/repay?date=garbage").status_code == 400


# ---- 대시보드 principal_remaining ----


@pytest.mark.django_db(transaction=True)
def test_dashboard_principal_remaining_tracks_unpaid_schedule(
    auth_api, user, product
):
    from api.adminpanel.services import execute_loan
    from jobs.tasks import repay_daily

    fund(user, 10_000_000)
    inv, _ = invest_services.place_investment(user, product.id, 3_000_000)
    product.raised_amount = product.target_amount
    product.status = Product.Status.RECRUITED
    product.save(update_fields=["raised_amount", "status"])
    execute_loan(product)
    first = inv.schedules.get(seq=1)
    repay_daily(run_date=first.due_date + timedelta(days=1))
    second = inv.schedules.get(seq=2)

    r = auth_api.get("/api/me/dashboard")
    assert r.status_code == 200
    assert (
        r.json()["active"]["principal_remaining"] == second.principal_balance
    )


# ---- 비번 리셋 세션 폐기 ----


@pytest.mark.django_db(transaction=True)
def test_password_reset_revokes_sessions(api, user):
    from api.common.auth import issue_reauth_token

    r = api.post(
        "/api/auth/login",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    assert r.status_code == 200
    csrf = api.cookies["csrf"].value
    issue_reauth_token(user)
    stale = account_services.issue_password_reset_token(user)
    fresh = account_services.issue_password_reset_token(user)

    r = api.post(
        "/api/auth/password/reset",
        {"token": fresh.token, "new_password": "NewPass123!"},
        format="json",
        HTTP_X_CSRF_TOKEN=csrf,
    )
    assert r.status_code == 200

    r = api.post("/api/auth/refresh", format="json", HTTP_X_CSRF_TOKEN=csrf)
    assert r.status_code == 401
    assert not ReauthToken.objects.filter(user=user).exists()
    stale.refresh_from_db()
    assert stale.used is True


# ---- seed 엔드포인트 ----


@pytest.mark.django_db
def test_seed_products_404_when_debug_off(staff_api):
    with override_settings(DEBUG=False):
        r = staff_api.post("/api/admin/seed/products", {"count": 1}, format="json")
    assert r.status_code == 404


@pytest.mark.django_db
def test_seed_products_rejects_bad_params(staff_api):
    with override_settings(DEBUG=True):
        assert (
            staff_api.post(
                "/api/admin/seed/products", {"count": "abc"}, format="json"
            ).status_code
            == 400
        )


# ---- admin grade-request serializer ----


@pytest.mark.django_db
def test_grade_decision_rejects_invalid_action(staff_api, user):
    gr = GradeRequest.objects.create(user=user, to_grade="professional")
    r = staff_api.patch(
        f"/api/admin/grade-requests/{gr.id}", {"action": "bogus"}, format="json"
    )
    assert r.status_code == 400
    r = staff_api.patch(
        f"/api/admin/grade-requests/{gr.id}", {"action": "approve"}, format="json"
    )
    assert r.status_code == 200
    user.refresh_from_db()
    assert user.grade == "professional"


# ---- refinance_of setattr ----


@pytest.mark.django_db
def test_admin_product_patch_refinance_of(staff_api, product):
    refi = Product.objects.create(
        product_no="2026-refi",
        name="리파이낸스",
        type=Product.Type.SCF,
        annual_rate="8.00",
        term_months=6,
        target_amount=5_000_000,
        repay_type=Product.RepayType.BULLET,
        borrower_id="borrower-9",
        status=Product.Status.DRAFT,
    )
    r = staff_api.patch(
        f"/api/admin/products/{refi.id}",
        {"refinance_of": product.id},
        format="json",
    )
    assert r.status_code == 200
    refi.refresh_from_db()
    assert refi.refinance_of_id == product.id


@pytest.mark.django_db
def test_admin_product_create_refinance_of(staff_api, product):
    r = staff_api.post(
        "/api/admin/products",
        {
            "name": "리파이낸스2",
            "type": "scf",
            "annual_rate": "8.00",
            "term_months": 6,
            "target_amount": 5_000_000,
            "repay_type": "bullet",
            "borrower_id": "b-1",
            "refinance_of": product.id,
        },
        format="json",
    )
    assert r.status_code == 201
    assert Product.objects.get(pk=r.json()["id"]).refinance_of_id == product.id


# ---- product_no 레이스 ----


@pytest.mark.django_db
def test_admin_product_create_distinct_product_no(staff_api):
    payload = {
        "name": "상품",
        "type": "scf",
        "annual_rate": "8.00",
        "term_months": 6,
        "target_amount": 5_000_000,
        "repay_type": "bullet",
        "borrower_id": "b-1",
    }
    r1 = staff_api.post("/api/admin/products", payload, format="json")
    r2 = staff_api.post("/api/admin/products", payload, format="json")
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["product_no"] != r2.json()["product_no"]


# ---- broad except REFUNDED 축소 ----


def _reservation_setup(user, product):
    fund(user, 10_000_000)
    inv, _ = invest_services.place_investment(user, product.id, 100_000)
    inv.status = Investment.Status.ACTIVE
    inv.save(update_fields=["status"])
    res = Reservation.objects.create(investment=inv, amount=100_000)
    refi = Product.objects.create(
        product_no="2026-refi2",
        name="리파이낸스",
        type=Product.Type.SCF,
        annual_rate="8.00",
        term_months=6,
        target_amount=5_000_000,
        repay_type=Product.RepayType.BULLET,
        borrower_id="borrower-9",
        status=Product.Status.RECRUITING,
        refinance_of=product,
    )
    return res, refi


@pytest.mark.django_db(transaction=True)
def test_convert_reservations_refunds_business_error(user, product, monkeypatch):
    res, refi = _reservation_setup(user, product)

    def boom(*a, **kw):
        raise ValidationFailed("limit")

    monkeypatch.setattr(invest_services, "place_investment", boom)
    out = invest_services.convert_reservations(refi)
    assert out["refunded"] == 1
    res.refresh_from_db()
    assert res.status == Reservation.Status.REFUNDED


@pytest.mark.django_db(transaction=True)
def test_convert_reservations_propagates_unexpected_error(
    user, product, monkeypatch
):
    res, refi = _reservation_setup(user, product)

    def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(invest_services, "place_investment", boom)
    with pytest.raises(RuntimeError):
        invest_services.convert_reservations(refi)
    res.refresh_from_db()
    assert res.status == Reservation.Status.RESERVED


# ---- refresh is_active ----


@pytest.mark.django_db(transaction=True)
def test_refresh_rejects_inactive_user(api, user):
    r = api.post(
        "/api/auth/login",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    assert r.status_code == 200
    csrf = api.cookies["csrf"].value
    user.is_active = False
    user.save(update_fields=["is_active"])
    r = api.post("/api/auth/refresh", format="json", HTTP_X_CSRF_TOKEN=csrf)
    assert r.status_code == 401


# ---- PIN 카운터 lost-update ----


@pytest.mark.django_db(transaction=True)
def test_pin_failures_not_lost_under_concurrency(user):
    account_services.register_pin(user, "123456")

    def bad(_):
        return account_services.check_pin(user, "000000")

    with ThreadPoolExecutor(2) as ex:
        list(ex.map(bad, range(2)))
    user.refresh_from_db()
    assert user.pin_failures == 2


# ---- SSE 커서 ----


@pytest.mark.django_db
def test_progress_trigger_serializes_inserts(db):
    from django.db import connection

    with connection.cursor() as c:
        c.execute(
            "SELECT pg_get_functiondef('product_progress_event()'::regprocedure)"
        )
        src = c.fetchone()[0]
    assert "pg_advisory_xact_lock" in src
    assert "hashtext(NEW.id::text)" not in src


# ---- 스로틀 ----


@pytest.mark.django_db
def test_login_throttled(api, user, monkeypatch):
    monkeypatch.setitem(
        settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "auth", "1/min"
    )
    body = {"email": user.email, "password": PASSWORD}
    assert api.post("/api/auth/login", body, format="json").status_code == 200
    api.cookies.clear()
    assert api.post("/api/auth/login", body, format="json").status_code == 429


@pytest.mark.django_db
def test_reset_request_throttled(api, user, monkeypatch):
    monkeypatch.setitem(
        settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "reset", "1/min"
    )
    body = {"email": user.email}
    assert (
        api.post("/api/auth/password/reset-request", body, format="json").status_code
        == 200
    )
    assert (
        api.post("/api/auth/password/reset-request", body, format="json").status_code
        == 429
    )


@pytest.mark.django_db
def test_vitals_throttled(api, monkeypatch):
    monkeypatch.setitem(
        settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "vitals", "1/min"
    )
    body = {"name": "lcp", "value": 1.0, "path": "/", "ts": 1.0}
    assert api.post("/api/metrics/vitals", body, format="json").status_code == 204
    assert api.post("/api/metrics/vitals", body, format="json").status_code == 429
