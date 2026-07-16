from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx

DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 10.0


class ApiError(RuntimeError):
    """Raised when the booking API cannot be reached or returns an error."""


def get_api_base_url() -> str:
    return os.getenv("API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")


def get_health(*, base_url: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    url = f"{base_url or get_api_base_url()}/health"
    try:
        response = httpx.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise ApiError(
            f"Backend unreachable at {url}. "
            "Start it with: cd backend && .venv/Scripts/python.exe -m uvicorn "
            "app.main:app --reload --host 127.0.0.1 --port 8000"
        ) from exc


def list_bookings(
    start_at: datetime,
    end_at: datetime,
    *,
    status: str = "confirmed",
    base_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    url = f"{base_url or get_api_base_url()}/bookings"
    params: dict[str, str] = {
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "status": status,
    }
    try:
        response = httpx.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ApiError("Unexpected /bookings response shape")
        return data
    except httpx.HTTPError as exc:
        raise ApiError(f"Failed to load bookings from {url}: {exc}") from exc
