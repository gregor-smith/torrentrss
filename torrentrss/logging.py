from __future__ import annotations

import sys
from logging import Logger, StreamHandler, Formatter

from .utils import wrap_for_asyncio
from .constants import NAME, LOG_MESSAGE_FORMAT


_logger = Logger(name=NAME)


def configure(level: str) -> None:
    handler = StreamHandler(stream=sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(Formatter(fmt=LOG_MESSAGE_FORMAT))
    _logger.setLevel(level)
    _logger.addHandler(handler)


debug = wrap_for_asyncio(_logger.debug)
info = wrap_for_asyncio(_logger.info)
warning = wrap_for_asyncio(_logger.warning)
error = wrap_for_asyncio(_logger.error)
exception = wrap_for_asyncio(_logger.exception)
