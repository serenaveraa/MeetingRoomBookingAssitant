from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


def emit_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit one JSON log line using the standard-library logging stack."""
    payload = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    logger.log(level, json.dumps(payload, default=str, sort_keys=True))
