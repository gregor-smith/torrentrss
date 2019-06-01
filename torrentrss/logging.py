from __future__ import annotations

import sys
from logging import Logger, StreamHandler, Formatter

from .constants import NAME, LOG_MESSAGE_FORMAT


def configure_logging(level: str) -> None:
    handler = StreamHandler(stream=sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(Formatter(fmt=LOG_MESSAGE_FORMAT))
    logger.setLevel(level)
    logger.addHandler(handler)


logger = Logger(name=NAME)
