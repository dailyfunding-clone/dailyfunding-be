import json
import logging
import time

import pytest
from django.db import transaction

from api.adminpanel.services import execute_loan
from api.investments.services import place_investment
from api.products import views
from api.products.models import Product
from jobs.tasks import repay_daily
from tests.conftest import fund


def event(response):
    frame = next(response.streaming_content).decode()
    lines = dict(line.split(": ", 1) for line in frame.strip().splitlines())
    assert lines["event"] == "progress"
    return int(lines["id"]), json.loads(lines["data"])


def test_vitals_logs_valid_beacon(api, caplog):
    payload = {"name": "LCP", "value": 123.5, "path": "/investment", "ts": 1790000000000}
    with caplog.at_level(logging.INFO, logger="api.metrics"):
        response = api.post("/api/metrics/vitals", payload, format="json")
    assert response.status_code == 204
    assert response.content == b""
    assert json.loads(caplog.records[-1].message) == payload


@pytest.mark.parametrize("payload", [
    {},
    {"name": "LCP", "value": "oops", "path": "/", "ts": 1},
    {"name": "LCP", "value": 1, "path": "/", "ts": None},
    {"name": "LCP", "value": "Infinity", "path": "/", "ts": 1},
])
def test_vitals_rejects_invalid_payload(api, payload):
    assert api.post("/api/metrics/vitals", payload, format="json").status_code == 400


@pytest.mark.django_db(transaction=True)
def test_stream_replays_investment_and_repayment(api, user, product):
    product.term_months = 1
    product.target_amount = 100_000
    product.save()
    response = api.get(f"/api/products/stream?ids={product.id}")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/event-stream")
    assert response["X-Accel-Buffering"] == "no"
    cursor, data = event(response)
    response.close()
    assert data == {"id": product.id, "raised_amount": 0, "remaining": 100_000, "status": "recruiting"}

    fund(user, 100_000)
    investment, _ = place_investment(user, product.id, 100_000)
    execute_loan(product)
    repay_daily(run_date=investment.schedules.get().due_date)

    response = api.get(f"/api/products/stream?ids={product.id}", HTTP_LAST_EVENT_ID=str(cursor))
    try:
        frames = [event(response) for _ in range(3)]
        assert [data["status"] for _, data in frames] == ["recruited", "repaying", "repaid"]
        assert all(data["raised_amount"] == 100_000 and data["remaining"] == 0 for _, data in frames)
        assert cursor < frames[0][0] < frames[1][0] < frames[2][0]
    finally:
        response.close()


@pytest.mark.django_db(transaction=True)
def test_stream_filters_and_emits_committed_changes_via_notify(api, product, monkeypatch):
    def boom(_):
        raise AssertionError("stream must not sleep-poll")

    monkeypatch.setattr(time, "sleep", boom)
    Product.objects.create(product_no="other", name="other", type="scf", annual_rate=9, term_months=1, target_amount=50, repay_type="bullet", borrower_id="other", status="recruiting")
    response = api.get(f"/api/products/stream?ids={product.id}")
    assert response.status_code == 200
    try:
        assert event(response)[1]["id"] == product.id
        with pytest.raises(ValueError), transaction.atomic():
            Product.objects.filter(pk=product.id).update(raised_amount=999)
            raise ValueError("rollback")
        Product.objects.filter(pk=product.id).update(raised_amount=100_000)
        _, data = event(response)
        assert data == {"id": product.id, "raised_amount": 100_000, "remaining": 9_900_000, "status": "recruiting"}
    finally:
        response.close()


@pytest.mark.django_db(transaction=True)
def test_stream_heartbeat_on_notify_timeout(api, product, monkeypatch):
    monkeypatch.setattr(views, "HEARTBEAT_SEC", 0.05)
    response = api.get(f"/api/products/stream?ids={product.id}")
    assert response.status_code == 200
    try:
        event(response)
        assert next(response.streaming_content) == b": heartbeat\n\n"
    finally:
        response.close()


@pytest.mark.django_db(transaction=True)
def test_stream_closes_notify_connection_on_disconnect(api, product, monkeypatch):
    conns = []
    original = views._listen_connection
    monkeypatch.setattr(
        views,
        "_listen_connection",
        lambda: conns.append(original()) or conns[-1],
    )
    response = api.get(f"/api/products/stream?ids={product.id}")
    assert response.status_code == 200
    try:
        event(response)
    finally:
        response.close()
    assert conns and conns[0].closed


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("query,cursor", [("ids=no", "0"), ("ids=-1", "0"), ("ids=1,", "0"), ("ids=1", "bad"), ("ids=1", "-1"), ("ids=9223372036854775808", "0")])
def test_stream_rejects_invalid_ids_and_cursor(api, query, cursor):
    assert api.get(f"/api/products/stream?{query}", HTTP_LAST_EVENT_ID=cursor).status_code == 400
