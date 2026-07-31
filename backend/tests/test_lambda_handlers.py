"""Lambda entrypoint smoke tests (no AWS deploy required)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db import get_engine, reset_engine


@pytest.fixture(autouse=True)
def _clear_settings(monkeypatch):
    get_settings.cache_clear()
    reset_engine()
    yield
    reset_engine()
    get_settings.cache_clear()


def test_api_handler_is_mangum_wrapper():
    from mangum import Mangum

    from app.lambda_handlers import api_handler

    assert isinstance(api_handler, Mangum)


def test_reminder_handler_runs_vacate_job_and_returns_json():
    from app.lambda_handlers import reminder_handler

    with patch(
        "app.lambda_handlers.run_vacate_reminder_job",
        return_value=2,
    ) as mock_job:
        result = reminder_handler({"source": "aws.events"}, MagicMock())

    mock_job.assert_called_once_with()
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"sent": 2}


def test_lambda_engine_uses_null_pool(monkeypatch, tmp_path):
    db_path = tmp_path / "lambda.db"
    monkeypatch.setenv("RUNNING_IN_LAMBDA", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()

    engine = get_engine()
    assert isinstance(engine.pool, NullPool)


def test_lifespan_skips_scheduler_when_running_in_lambda(monkeypatch):
    monkeypatch.setenv("RUNNING_IN_LAMBDA", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    get_settings.cache_clear()
    reset_engine()

    from fastapi.testclient import TestClient

    from app.main import app

    with patch("app.main.create_scheduler") as mock_create:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
        mock_create.assert_not_called()


def test_lifespan_starts_scheduler_locally(monkeypatch):
    monkeypatch.setenv("RUNNING_IN_LAMBDA", "false")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    get_settings.cache_clear()
    reset_engine()

    from fastapi.testclient import TestClient

    from app.main import app

    fake = MagicMock()
    with patch("app.main.create_scheduler", return_value=fake) as mock_create:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
        mock_create.assert_called_once()
        fake.start.assert_called_once()
        fake.shutdown.assert_called_once_with(wait=False)
