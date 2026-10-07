"""structlog configuration: one call at CLI start, UTC timestamps, key=value console output."""

import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog for console output.

    Args:
        level: Standard logging level name.
    """
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        # Reason: the status table goes to stdout; logs must not interleave with it.
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
