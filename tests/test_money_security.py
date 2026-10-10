"""money/security 회귀 테스트."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.conf import settings
from django.db import connections
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from api.adminpanel import services as admin
from api.common.exceptions import StateConflict
from api.investments.services import place_investment
from api.ledger import services as ledger
from api.ledger.models import DepositIntent, LedgerEntry, Withdrawal
from api.loans.models import LoanApplication
from api.products.models import Product
from api.webhooks.models import WebhookEvent
from tests.conftest import (
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
    r = api.post("/api/auth/refresh", {"refresh": old_refresh}, format="json")
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
def test_withdraw_quota_counts_completed_only(user):
    fund(user, 10_000_000)
    for _ in range(settings.WITHDRAW_FREE_COUNT):
        ledger.create_withdrawal(user, 100)
    wd = ledger.create_withdrawal(user, 100)
    assert wd.fee == 0

    Withdrawal.objects.filter(user=user).update(
        status=Withdrawal.Status.COMPLETED
    )
    wd = ledger.create_withdrawal(user, 100)
    assert wd.fee == settings.WITHDRAW_FEE


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
