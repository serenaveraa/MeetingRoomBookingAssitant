"""AWS Lambda entrypoints for the API and vacate-reminder functions.

Local development continues to use ``uvicorn app.main:app``. On AWS:

- API Lambda: ``app.lambda_handlers.api_handler``
- Reminder Lambda: ``app.lambda_handlers.reminder_handler`` (EventBridge)

Set ``RUNNING_IN_LAMBDA=true`` on both functions.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mangum import Mangum

from app.main import app
from app.scheduler.vacate_reminders import run_vacate_reminder_job

logger = logging.getLogger(__name__)

# Lifespan still runs init_db on cold start; APScheduler is gated in main.py.
api_handler = Mangum(app, lifespan="auto")


def reminder_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """EventBridge target: run the idempotent vacate-reminder job once."""
    sent = run_vacate_reminder_job()
    logger.info("lambda.vacate_reminder sent=%s event_keys=%s", sent, list(event.keys()))
    return {
        "statusCode": 200,
        "body": json.dumps({"sent": sent}),
    }
