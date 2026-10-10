"""money/security 회귀 테스트."""
import importlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from api.accounts import services as account_services
from api.accounts.models import GradeRequest, User
from api.adminpanel import services as admin
from api.common.exceptions import PinLocked, StateConflict
from api.investments.models import Investment, RepaymentSchedule
from api.investments.services import place_investment
from api.ledger import services as ledger
from api.ledger.models import DepositIntent, LedgerEntry, PointEntry, Withdrawal
from api.loans.models import LoanApplication
from api.products.models import Product
from api.webhooks.models import WebhookEvent
from jobs.tasks import repay_daily
from tests.conftest import (
    AGREEMENTS,
    PASSWORD,
    deposit_webhook_payload,
    fund,
    signed_post,
)


@pytest.mark.django_db
def test_mockbank_views_404_when_not_debug(api):
    with override_settings(DEBUG=False):
        r = api.post(
            "/mockbank/deposits/execute",
            {"account_no": "x", "amount": 1, "sender_name": "y"},
            format="json",
        )
        assert r.status_code == 404
        r = api.post(
            "/mockbank/transfers/execute", {"withdrawal_id": "wd-1"}, format="json"
        )
        assert r.status_code == 404
        r = api.get("/mockbank/deliveries")
        assert r.status_code == 404

    with override_settings(DEBUG=True):
        r = api.get("/mockbank/deliveries")
        assert r.status_code == 200


@pytest.mark.django_db
def test_password_reset_dev_token_only_in_debug(api, user):
    with override_settings(DEBUG=True):
        r = api.post(
            "/api/auth/password/reset-request",
            {"email": user.email},
            format="json",
        )
        assert r.status_code == 200
        assert r.json()["dev_token"]

    with override_settings(DEBUG=False):
        r = api.post(
            "/api/auth/password/reset-request",
            {"email": user.email},
            format="json",
        )
        assert r.status_code == 200
        assert r.json()["dev_token"] is None


@pytest.mark.django_db
def test_login_response_has_no_tokens_in_body(api, user):
    r = api.post(
        "/api/auth/login", {"email": user.email, "password": PASSWORD}, format="json"
    )
    assert r.status_code == 200
    assert "access" in r.cookies and "refresh" in r.cookies
    assert "access_token" not in r.json()
    assert "refresh_token" not in r.json()


@pytest.mark.django_db
def test_refresh_blacklists_presented_token(api, user):
    r = api.post(
        "/api/auth/login", {"email": user.email, "password": PASSWORD}, format="json"
    )
    old_refresh = r.cookies["refresh"].value
    r = api.post(
        "/api/auth/refresh",
        {"refresh": old_refresh},
        format="json",
        HTTP_X_CSRF_TOKEN=api.cookies["csrf"].value,
    )
    assert r.status_code == 200
    assert "access_token" not in r.json()
    outstanding = OutstandingToken.objects.get(token=old_refresh)
    assert BlacklistedToken.objects.filter(token=outstanding).exists()


@pytest.mark.django_db
def test_logout_keeps_pin(auth_api, user):
    r = auth_api.post("/api/auth/pin", {"pin": "123456"}, format="json")
    assert r.status_code == 200
    user.refresh_from_db()
    assert user.pin_registered

    r = auth_api.post("/api/auth/logout")
    assert r.status_code == 200
    user.refresh_from_db()
    assert user.pin_registered


@pytest.mark.django_db
def test_identity_verify_requires_auth(api):
    r = api.post(
        "/api/auth/identity/verify",
        {
            "carrier": "SKT",
            "name": "홍길동",
            "birth": "19900101",
            "phone": "01011112222",
            "email": "victim@test.local",
        },
        format="json",
    )
    assert r.status_code == 401


@pytest.mark.django_db
def test_identity_verify_binds_request_user(api, auth_api, user, other_user):
    r = auth_api.post(
        "/api/auth/identity/verify",
        {
            "carrier": "SKT",
            "name": "새이름",
            "birth": "19990101",
            "phone": "01099998888",
            "email": other_user.email,
        },
        format="json",
    )
    assert r.status_code == 200
    user.refresh_from_db()
    other_user.refresh_from_db()
    assert user.identity.name == "새이름"


@pytest.mark.django_db
def test_history_views_scoped_to_user(api, auth_api, user, other_user):
    ledger.post(
        LedgerEntry.Kind.REPAY,
        [
            (ledger.borrower_acc(1), -1_000_000),
            (ledger.deposit_acc(other_user.id), 900_000),
            (ledger.PAYABLE_TAX, 80_000),
            (ledger.REVENUE_FEE, 20_000),
        ],
        ref_type="repayment_schedule",
        ref_id="999",
    )
    r = auth_api.get("/api/deposit/history?view=withholding")
    assert r.json()["results"] == []
    r = auth_api.get("/api/deposit/history?view=platform_fee")
    assert r.json()["results"] == []

    other_client = APIClient()
    other_client.force_authenticate(user=other_user)
    r = other_client.get("/api/deposit/history?view=withholding")
    assert len(r.json()["results"]) == 1
    r = other_client.get("/api/deposit/history?view=platform_fee")
    assert len(r.json()["results"]) == 1


@pytest.mark.django_db
def test_webhook_failure_marks_failed_and_retry_reprocesses(api, user):
    va = user.virtual_account
    DepositIntent.objects.create(
        id=DepositIntent.new_id(),
        user=user,
        amount=1_000_000,
        sender_name=va.holder,
    )
    client = APIClient(raise_request_exception=False)
    bad = deposit_webhook_payload(va.account_no, va.holder, "not-a-number")
    r = signed_post(client, "/api/webhooks/bank/deposit", bad)
    assert r.status_code == 400
    event = WebhookEvent.objects.get(event_id=bad["event_id"])
    assert event.status == WebhookEvent.Status.FAILED

    good = {**bad, "amount": 1_000_000}
    r = signed_post(client, "/api/webhooks/bank/deposit", good)
    assert r.status_code == 200
    event.refresh_from_db()
    assert event.status == WebhookEvent.Status.PROCESSED
    assert ledger.deposit_balance(user.id) == 1_000_000

    r = signed_post(client, "/api/webhooks/bank/deposit", good)
    assert r.status_code == 200
    assert r.json().get("deduplicated") is True
    assert ledger.deposit_balance(user.id) == 1_000_000


@pytest.mark.django_db
def test_withdraw_quota_counts_requested_and_completed(user):
    fund(user, 10_000_000)
    for _ in range(settings.WITHDRAW_FREE_COUNT):
        ledger.create_withdrawal(user, 100)
    wd = ledger.create_withdrawal(user, 100)
    assert wd.fee == settings.WITHDRAW_FEE


def test_settings_fail_fast_without_required_env(monkeypatch):
    import config.settings as cfg

    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(ImproperlyConfigured):
        importlib.reload(cfg)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("BANK_WEBHOOK_SECRET", "test-bank-secret")
    importlib.reload(cfg)


@pytest.mark.django_db
def test_product_stream_per_ip_cap(api):
    from api.products import views as product_views

    product_views._stream_counts.clear()
    responses = [api.get("/api/products/stream") for _ in range(3)]
    assert all(r.status_code == 200 for r in responses)
    r = api.get("/api/products/stream")
    assert r.status_code == 429
    responses[0].close()
    r = api.get("/api/products/stream")
    assert r.status_code == 200
    r.close()
    product_views._stream_counts.clear()


@pytest.mark.django_db
def test_auth_cookies_secure_flag_and_csrf_cookie(api, user):
    with override_settings(DEBUG=False):
        r = api.post(
            "/api/auth/login",
            {"email": user.email, "password": PASSWORD},
            format="json",
        )
    assert r.status_code == 200
    assert r.cookies["access"]["secure"] is True
    assert r.cookies["refresh"]["secure"] is True
    assert r.cookies["csrf"].value
    assert not r.cookies["csrf"]["httponly"]


@pytest.mark.django_db
def test_cookie_auth_mutations_require_csrf(api, user):
    r = api.post(
        "/api/auth/login",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    assert r.status_code == 200
    csrf = api.cookies["csrf"].value
    body = {"new_product": False}

    r = api.post("/api/notifications/settings", body, format="json")
    assert r.status_code == 403
    r = api.post(
        "/api/notifications/settings",
        body,
        format="json",
        HTTP_X_CSRF_TOKEN="bogus",
    )
    assert r.status_code == 403
    r = api.post(
        "/api/notifications/settings",
        body,
        format="json",
        HTTP_X_CSRF_TOKEN=csrf,
    )
    assert r.status_code == 200
    r = api.get("/api/notifications/settings")
    assert r.status_code == 200


@pytest.mark.django_db
def test_bearer_auth_not_csrf_blocked(api, user):
    r = api.post(
        "/api/auth/login",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    access = api.cookies["access"].value
    bare = APIClient()
    r = bare.post(
        "/api/notifications/settings",
        {"new_product": False},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
    )
    assert r.status_code == 200


@pytest.mark.django_db
def test_self_referral_gets_no_points(api, user):
    r = api.post(
        "/api/auth/signup",
        {
            "email": user.email,
            "password": PASSWORD,
            "agreements": AGREEMENTS,
            "referrer_email": user.email,
        },
        format="json",
    )
    assert r.status_code == 409
    api.post(
        "/api/auth/signup",
        {
            "email": "newbie@test.local",
            "password": PASSWORD,
            "agreements": AGREEMENTS,
            "referrer_email": "newbie@test.local",
        },
        format="json",
    )
    newbie = User.objects.get(email="newbie@test.local")
    assert ledger.point_balance(newbie.id) == 0
    assert not PointEntry.objects.filter(
        user=newbie, ref_type="referral"
    ).exists()


@pytest.mark.django_db
def test_referral_reward_capped(user):
    for i in range(settings.REFERRAL_MAX_REWARDS + 2):
        referred = User.objects.create_user(f"referred{i}@test.local", PASSWORD)
        account_services.grant_referral_reward(user.email, referred)
    assert (
        PointEntry.objects.filter(user=user, ref_type="referral").count()
        == settings.REFERRAL_MAX_REWARDS
    )


@pytest.mark.django_db
def test_register_pin_keeps_failure_lockout(user):
    account_services.register_pin(user, "123456")
    for _ in range(settings.PIN_MAX_FAILURES - 1):
        assert account_services.check_pin(user, "000000") is False
    with pytest.raises(PinLocked):
        account_services.check_pin(user, "000000")

    account_services.register_pin(user, "654321")
    user.refresh_from_db()
    assert user.pin_failures == settings.PIN_MAX_FAILURES
    assert user.pin_locked_at is not None
    with pytest.raises(PinLocked):
        account_services.check_pin(user, "654321")


@pytest.mark.django_db
def test_deposit_webhook_transfer_level_dedup(api, user):
    va = user.virtual_account
    DepositIntent.objects.create(
        id=DepositIntent.new_id(),
        user=user,
        amount=1_000_000,
        sender_name=va.holder,
    )
    payload = deposit_webhook_payload(va.account_no, va.holder, 1_000_000)
    r = signed_post(api, "/api/webhooks/bank/deposit", payload)
    assert r.status_code == 200
    assert ledger.deposit_balance(user.id) == 1_000_000

    replay = deposit_webhook_payload(va.account_no, va.holder, 1_000_000)
    assert replay["event_id"] != payload["event_id"]
    r = signed_post(api, "/api/webhooks/bank/deposit", replay)
    assert r.status_code == 200
    assert ledger.deposit_balance(user.id) == 1_000_000
    assert DepositIntent.objects.filter(user=user).count() == 1


@pytest.mark.django_db(transaction=True)
def test_repay_daily_skips_non_repaying_product(user, product):
    fund(user, 50_000_000)
    inv, rows = place_investment(user, product.id, 2_000_000)
    assert product.status == Product.Status.RECRUITING
    s = inv.schedules.get(seq=1)
    s.due_date = timezone.now().date()
    s.save(update_fields=["due_date"])

    before = ledger.deposit_balance(user.id)
    r = repay_daily(run_date=timezone.now().date())
    assert r["paid"] == 0
    s.refresh_from_db()
    assert s.status == RepaymentSchedule.Status.SCHEDULED
    assert ledger.deposit_balance(user.id) == before


@pytest.mark.django_db(transaction=True)
def test_repay_daily_collects_overdue_product(user, product):
    fund(user, 50_000_000)
    inv, rows = place_investment(user, product.id, 2_000_000)
    inv.status = Investment.Status.OVERDUE
    inv.save(update_fields=["status"])
    product.status = Product.Status.OVERDUE
    product.save(update_fields=["status"])
    s = inv.schedules.get(seq=1)
    s.status = RepaymentSchedule.Status.OVERDUE
    s.due_date = timezone.now().date() - timedelta(days=1)
    s.save(update_fields=["status", "due_date"])

    before = ledger.deposit_balance(user.id)
    r = repay_daily(run_date=timezone.now().date())
    assert r["paid"] == 1
    s.refresh_from_db()
    assert s.status == RepaymentSchedule.Status.PAID
    assert ledger.deposit_balance(user.id) > before


@pytest.mark.django_db
def test_notification_settings_get(auth_api, user):
    r = auth_api.get("/api/notifications/settings")
    assert r.status_code == 200
    assert r.json() == {"enabled": True}
    auth_api.post(
        "/api/notifications/settings",
        {"new_product": False, "recruit_closed": False, "repayment": False},
        format="json",
    )
    r = auth_api.get("/api/notifications/settings")
    assert r.json() == {"enabled": False}


@pytest.mark.django_db
def test_admin_grade_and_loan_status_filter(staff_api, user):
    GradeRequest.objects.create(user=user, to_grade="income_eligible")
    GradeRequest.objects.create(
        user=user, to_grade="professional", status="rejected"
    )
    LoanApplication.objects.create(
        name="a", phone="010", email="a@b.c", amount=100
    )
    LoanApplication.objects.create(
        name="b", phone="010", email="b@b.c", amount=200, status="rejected"
    )
    r = staff_api.get("/api/admin/grade-requests?status=submitted")
    assert len(r.json()["results"]) == 1
    r = staff_api.get("/api/admin/loan-applications?status=rejected")
    assert len(r.json()["results"]) == 1
    assert r.json()["results"][0]["name"] == "b"


@pytest.mark.django_db(transaction=True)
def test_concurrent_withdrawals_serialized(user):
    fund(user, 1_000_000)

    def attempt(_):
        try:
            return ledger.create_withdrawal(user, 800_000)
        except Exception as e:
            return e
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    succeeded = [r for r in results if isinstance(r, Withdrawal)]
    assert len(succeeded) == 1
    assert ledger.deposit_balance(user.id) == 200_000


@pytest.mark.django_db
def test_execute_loan_requires_recruited(user, product):
    fund(user, 10_000_000)
    with pytest.raises(StateConflict):
        admin.execute_loan(product)


@pytest.mark.django_db
def test_execute_loan_recruited_to_repaying(user, product):
    from api.accounts.models import User

    user.grade = User.Grade.PROFESSIONAL
    user.save(update_fields=["grade"])
    fund(user, 10_000_000)
    place_investment(user, product.id, 4_000_000)
    product.raised_amount = product.target_amount
    product.status = Product.Status.RECRUITED
    product.save(update_fields=["raised_amount", "status"])

    p = admin.execute_loan(product)
    assert p.status == Product.Status.REPAYING
    assert ledger.balance(ledger.borrower_acc(product.id)) == product.target_amount


@pytest.mark.django_db
def test_loan_decision_validates_payload(staff_api):
    app = LoanApplication.objects.create(
        name="홍길동", phone="010", email="a@b.c", amount=10_000_000
    )
    r = staff_api.patch(
        f"/api/admin/loan-applications/{app.id}",
        {"action": "bogus"},
        format="json",
    )
    assert r.status_code == 400
    r = staff_api.patch(
        f"/api/admin/loan-applications/{app.id}",
        {"action": "approve", "type": "not-a-type"},
        format="json",
    )
    assert r.status_code == 400


@pytest.mark.django_db
def test_admin_crud_validation(staff_api):
    body = {"year": 2026, "month": 1, "kpi": {}, "management": {}}
    r = staff_api.post("/api/admin/disclosures", body, format="json")
    assert r.status_code == 201
    pk = r.json()["id"]
    r = staff_api.post("/api/admin/disclosures", body, format="json")
    assert r.status_code == 400
    r = staff_api.post(
        "/api/admin/disclosures", {"year": 2026, "month": 13}, format="json"
    )
    assert r.status_code == 400
    r = staff_api.patch(
        f"/api/admin/disclosures/{pk}", {"month": 0}, format="json"
    )
    assert r.status_code == 400

    r = staff_api.post(
        "/api/admin/notices",
        {
            "category": "notice",
            "title": "t",
            "body": "b",
            "attachments": "not-an-array",
        },
        format="json",
    )
    assert r.status_code == 400


@pytest.mark.django_db
def test_admin_crud_patch_integrity_error(staff_api):
    staff_api.post("/api/admin/disclosures", {"year": 2026, "month": 2}, format="json")
    r2 = staff_api.post(
        "/api/admin/disclosures", {"year": 2026, "month": 3}, format="json"
    )
    pk = r2.json()["id"]
    r = staff_api.patch(
        f"/api/admin/disclosures/{pk}", {"month": 2}, format="json"
    )
    assert r.status_code == 400
