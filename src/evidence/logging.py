from __future__ import annotations

import json
import logging
from typing import Any


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    logger.log(level, json.dumps({"component": "evidence", "event": event, **fields},
                                 sort_keys=True, separators=(",", ":"), default=str))
