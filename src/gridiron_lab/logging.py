"""Logging conventions for Gridiron Lab.

- Loggers are children of ``gridiron_lab`` (``gridiron_lab.scoring``,
  ``gridiron_lab.config``).
- A stage summary is one INFO line with counts or a version stamp, not one
  line per player.
- DEBUG is for a single row when a developer asked to see it.
- WARNING means the stage continued in a degraded mode.
- ERROR is a failure that is about to be raised.
- Catch a specific exception type. Do not use ``except Exception`` or a bare
  ``except``.
- Never log secrets, tokens, or credential-bearing URLs.
"""

from __future__ import annotations

import logging

LOGGER_NAME = "gridiron_lab"


def get_logger(suffix: str | None = None) -> logging.Logger:
    """Return the package logger, or a ``gridiron_lab.<suffix>`` child."""

    if suffix:
        return logging.getLogger(f"{LOGGER_NAME}.{suffix}")
    return logging.getLogger(LOGGER_NAME)


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Attach one stderr handler to the package logger.

    Calling it again does not add a second handler. The logger still
    propagates, so test capture and application handlers both see records.
    """

    logger = get_logger()
    logger.setLevel(level)
    if not any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
    return logger
