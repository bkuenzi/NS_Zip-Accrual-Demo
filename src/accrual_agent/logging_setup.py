"""structlog configuration: JSON file log + readable console rendering."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog


def configure_logging(output_dir: str | Path = "output", json_file: bool = True) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if json_file:
        log_dir = Path(output_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "accrual_agent.jsonl"))

    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(message)s")

    def _route(logger: object, method: str, event_dict: dict) -> dict:
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _route,
            structlog.dev.ConsoleRenderer()
            if sys.stderr.isatty()
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
