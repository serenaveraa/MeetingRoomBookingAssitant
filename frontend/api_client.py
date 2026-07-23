from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx

DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 10.0
CHAT_TIMEOUT = 90.0


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


def get_utilization(
    start_date: datetime,
    end_date: datetime,
    *,
    base_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    url = f"{base_url or get_api_base_url()}/insights/utilization"
    params: dict[str, str] = {
       "start_date": start_date.isoformat(),
       "end_date": end_date.isoformat(),
    }
    try:
        response = httpx.get(url, params=params, timeout=timeout)
        if response.status_code >= 400:
            detail = _error_detail(response)
            raise ApiError(f"Utilization failed ({response.status_code}): {detail}")
        data = response.json()
        if not isinstance(data, dict):
            raise ApiError("Unexpected /insights/utilization response shape")
        return data
    except httpx.HTTPError as exc:
        raise ApiError(f"Failed to load utilization from {url}: {exc}") from exc


def post_chat(
    message: str,
    *,
    associate_email: str,
    associate_name: str,
    conversation_id: str | None = None,
    base_url: str | None = None,
    timeout: float = CHAT_TIMEOUT,
) -> dict[str, Any]:
    url = f"{base_url or get_api_base_url()}/agent/chat"
    payload: dict[str, Any] = {
        "message": message,
        "associate_email": associate_email,
        "associate_name": associate_name,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    try:
        response = httpx.post(url, json=payload, timeout=timeout)
        if response.status_code >= 400:
            detail = _error_detail(response)
            raise ApiError(f"Chat failed ({response.status_code}): {detail}")
        data = response.json()
        if not isinstance(data, dict):
            raise ApiError("Unexpected /agent/chat response shape")
        return data
    except httpx.HTTPError as exc:
        raise ApiError(f"Failed to reach chat API at {url}: {exc}") from exc


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        detail = body.get("detail", body)
        return str(detail)
    except Exception:
        return response.text or response.reason_phrase
