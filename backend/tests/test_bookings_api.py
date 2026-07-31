from datetime import datetime, timezone
from unittest.mock import ANY, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import init_db, reset_engine
from app.main import app
from app.models import BookingStatus
from app.services.booking import cancel_booking as service_cancel_booking


@pytest.fixture(autouse=True)
def _fresh_sqlite_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("ODC_TIMEZONE", "America/Sao_Paulo")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _utc(hour: int, minute: int = 0) -> str:
    return datetime(2026, 7, 16, hour, minute, tzinfo=timezone.utc).isoformat()


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_list_bookings(client: TestClient):
    create = client.post(
        "/bookings",
        json={
            "associate_email": "ada@example.com",
            "associate_name": "Ada",
            "start_at": _utc(10),
            "end_at": _utc(11),
            "purpose": "Standup",
        },
    )
    assert create.status_code == 201
    body = create.json()
    assert body["id"] > 0
    assert body["room_id"] == 1
    assert body["status"] == BookingStatus.confirmed.value
    assert body["start_at"].startswith("2026-07-16T10:00:00")
    assert body["end_at"].startswith("2026-07-16T11:00:00")
    assert body["purpose"] == "Standup"
    assert body["associate_email"] == "ada@example.com"

    listed = client.get(
        "/bookings",
        params={"start_at": _utc(9), "end_at": _utc(18)},
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == body["id"]


def test_availability_free_and_busy(client: TestClient):
    free = client.get(
        "/bookings/availability",
        params={"start_at": _utc(9), "end_at": _utc(10)},
    )
    assert free.status_code == 200
    assert free.json()["available"] is True
    assert free.json()["alternatives"] == []

    client.post(
        "/bookings",
        json={
            "associate_email": "ada@example.com",
            "associate_name": "Ada",
            "start_at": _utc(10),
            "end_at": _utc(11),
        },
    )
    busy = client.get(
        "/bookings/availability",
        params={"start_at": _utc(10), "end_at": _utc(11)},
    )
    assert busy.status_code == 200
    payload = busy.json()
    assert payload["available"] is False
    assert payload["conflict"] is not None
    assert len(payload["alternatives"]) >= 1


def test_extend_success_conflict_and_forbidden(client: TestClient):
    created = client.post(
        "/bookings",
        json={
            "associate_email": "ada@example.com",
            "associate_name": "Ada",
            "start_at": _utc(10),
            "end_at": _utc(11),
        },
    ).json()
    booking_id = created["id"]

    client.post(
        "/bookings",
        json={
            "associate_email": "grace@example.com",
            "associate_name": "Grace",
            "start_at": _utc(11),
            "end_at": _utc(12),
        },
    )

    forbidden = client.patch(
        f"/bookings/{booking_id}/extend",
        headers={"X-Associate-Email": "grace@example.com"},
        json={"minutes": 30},
    )
    assert forbidden.status_code == 403

    conflict = client.patch(
        f"/bookings/{booking_id}/extend",
        headers={"X-Associate-Email": "ada@example.com"},
        json={"minutes": 30},
    )
    assert conflict.status_code == 409

    # Free the next slot by not creating blocker for morning-only booking
    morning = client.post(
        "/bookings",
        json={
            "associate_email": "ada@example.com",
            "associate_name": "Ada",
            "start_at": _utc(14),
            "end_at": _utc(15),
        },
    ).json()
    extended = client.patch(
        f"/bookings/{morning['id']}/extend",
        headers={"X-Associate-Email": "ada@example.com"},
        json={"minutes": 30},
    )
    assert extended.status_code == 200
    end_at = datetime.fromisoformat(extended.json()["end_at"].replace("Z", "+00:00"))
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)
    assert end_at == datetime(2026, 7, 16, 15, 30, tzinfo=timezone.utc)


def test_cancel_success_and_forbidden(client: TestClient):
    created = client.post(
        "/bookings",
        json={
            "associate_email": "ada@example.com",
            "associate_name": "Ada",
            "start_at": _utc(10),
            "end_at": _utc(11),
        },
    ).json()
    booking_id = created["id"]

    forbidden = client.delete(
        f"/bookings/{booking_id}",
        headers={"X-Associate-Email": "other@example.com"},
    )
    assert forbidden.status_code == 403

    cancelled = client.delete(
        f"/bookings/{booking_id}",
        headers={"X-Associate-Email": "ada@example.com"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_cancel_api_delegates_to_booking_service(client: TestClient, monkeypatch):
    created = client.post(
        "/bookings",
        json={
            "associate_email": "ada@example.com",
            "associate_name": "Ada",
            "start_at": _utc(10),
            "end_at": _utc(11),
        },
    ).json()
    service_spy = MagicMock(wraps=service_cancel_booking)
    monkeypatch.setattr("app.api.bookings.cancel_booking", service_spy)

    response = client.delete(
        f"/bookings/{created['id']}",
        headers={"X-Associate-Email": "ada@example.com"},
    )

    assert response.status_code == 200
    assert service_spy.call_count == 1
    assert service_spy.call_args.args[1] == created["id"]
    assert "associate_id" in service_spy.call_args.kwargs


def test_create_waitlist_entry(client: TestClient):
    client.post(
        "/bookings",
        json={
            "associate_email": "ada@example.com",
            "associate_name": "Ada",
            "start_at": _utc(10),
            "end_at": _utc(11),
        },
    )
    created = client.post(
        "/waitlist",
        json={
            "associate_email": "grace@example.com",
            "associate_name": "Grace",
            "room_id": 1,
            "desired_start": _utc(10),
            "desired_end": _utc(11),
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["id"] > 0
    assert body["room_id"] == 1
    assert body["notified_at"] is None

    free = client.post(
        "/waitlist",
        json={
            "associate_email": "grace@example.com",
            "associate_name": "Grace",
            "room_id": 1,
            "desired_start": _utc(12),
            "desired_end": _utc(13),
        },
    )
    assert free.status_code == 400

    invalid = client.post(
        "/waitlist",
        json={
            "associate_email": "grace@example.com",
            "associate_name": "Grace",
            "room_id": 1,
            "desired_start": _utc(11),
            "desired_end": _utc(10),
        },
    )
    assert invalid.status_code == 400


def test_utilization_endpoint_returns_metrics(client: TestClient):
    client.post(
        "/bookings",
        json={
            "associate_email": "ada@example.com",
            "associate_name": "Ada",
            "start_at": datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc).isoformat(),
            "end_at": datetime(2026, 7, 16, 13, 0, tzinfo=timezone.utc).isoformat(),
            "purpose": "Standup",
        },
    )

    response = client.get(
        "/insights/utilization",
        params={"start_date": "2026-07-15", "end_date": "2026-07-17"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["booking_count"] == 1
    assert payload["avg_duration_minutes"] == 60.0
    assert payload["summary"] is not None


def test_utilization_endpoint_rejects_reversed_range(client: TestClient):
    response = client.get(
        "/insights/utilization",
        params={"start_date": "2026-07-17", "end_date": "2026-07-15"},
    )
    assert response.status_code == 400
    assert "end_date" in response.json()["detail"].lower()


def test_utilization_endpoint_handles_empty_range(client: TestClient):
    response = client.get(
        "/insights/utilization",
        params={"start_date": "2030-01-01", "end_date": "2030-01-03"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["booking_count"] == 0
    assert payload["bookings_per_day"]
    assert all(item["booking_count"] == 0 for item in payload["bookings_per_day"])


def test_openapi_includes_booking_paths(client: TestClient):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert "/bookings" in paths
    assert "/bookings/availability" in paths
    assert "/bookings/{booking_id}/extend" in paths
    assert "/bookings/{booking_id}" in paths
    assert "/health" in paths


def test_weekend_booking_and_availability_rejected(client: TestClient):
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Sao_Paulo")
    start = datetime(2026, 7, 18, 10, 0, tzinfo=tz).isoformat()  # Saturday
    end = datetime(2026, 7, 18, 11, 0, tzinfo=tz).isoformat()

    created = client.post(
        "/bookings",
        json={
            "associate_email": "ada@example.com",
            "associate_name": "Ada",
            "start_at": start,
            "end_at": end,
            "purpose": "Weekend",
        },
    )
    assert created.status_code == 400
    assert "weekend" in created.json()["detail"].lower()

    availability = client.get(
        "/bookings/availability",
        params={"start_at": start, "end_at": end},
    )
    assert availability.status_code == 400
    assert "weekend" in availability.json()["detail"].lower()
